from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)
from config import QDRANT_URL, QDRANT_COLLECTION, EMBEDDING_DIM

_client = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=QDRANT_URL)
    return _client


async def init_collection():
    """启动时检查 collection，不存在则创建"""
    client = _get_client()
    try:
        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        if QDRANT_COLLECTION in names:
            print(f"[Qdrant] Collection '{QDRANT_COLLECTION}' 已存在")
            return

        await client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        print(f"[Qdrant] Collection '{QDRANT_COLLECTION}' 创建成功")
    except Exception as e:
        print(f"[Qdrant] 初始化失败: {e}")


async def upsert_scene(
    scene_id: int,
    group_id: str,
    summary: str,
    participants: list,
    timestamp: str,
    vector: list[float],
):
    """把场景摘要写入向量库"""
    client = _get_client()
    try:
        await client.upsert(
            collection_name=QDRANT_COLLECTION,
            points=[
                PointStruct(
                    id=scene_id,  # 用 SQLite 的 id 做向量 id，方便关联
                    vector=vector,
                    payload={
                        "group_id": group_id,
                        "summary": summary,
                        "participants": participants,
                        "timestamp": timestamp,
                        "scene_id": scene_id,
                    },
                )
            ],
        )
        print(f"[Qdrant] 写入 scene_id={scene_id}, 摘要:{summary[:30]}...")
    except Exception as e:
        print(f"[Qdrant] 写入失败 scene_id={scene_id}: {e}")


async def search_scenes(
    group_id: str, query_vector: list[float], top_k: int = 3
) -> list[dict]:
    """在同群内语义检索相关场景"""
    client = _get_client()
    try:
        results = await client.search(
            collection_name=QDRANT_COLLECTION,
            query_vector=query_vector,
            query_filter=Filter(
                must=[FieldCondition(key="group_id", match=MatchValue(value=group_id))]
            ),
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "summary": r.payload.get("summary", ""),
                "participants": r.payload.get("participants", []),
                "timestamp": r.payload.get("timestamp", ""),
                "score": r.score,
            }
            for r in results
        ]
    except Exception as e:
        print(f"[Qdrant] 检索失败: {e}")
        return []
