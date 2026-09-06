from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
    PointIdsList,
)
from config import QDRANT_URL, QDRANT_COLLECTION, EMBEDDING_DIM

MEMES_COLLECTION = "memes"

_client = None


def _get_client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=QDRANT_URL)
    return _client


async def init_collection():
    """启动时检查 scenes collection，不存在则创建"""
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


async def init_memes_collection():
    """表情包语义索引集合"""
    client = _get_client()
    try:
        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        if MEMES_COLLECTION in names:
            print(f"[Qdrant] Collection '{MEMES_COLLECTION}' 已存在")
            return
        await client.create_collection(
            collection_name=MEMES_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        print(f"[Qdrant] Collection '{MEMES_COLLECTION}' 创建成功")
    except Exception as e:
        print(f"[Qdrant] memes 初始化失败: {e}")


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
                    id=scene_id,
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


# ========== 表情包索引 ==========


async def upsert_meme(
    point_id: int,
    filename: str,
    description: str,
    group_id: str,
    manual: bool,
    vector: list[float],
):
    client = _get_client()
    try:
        await client.upsert(
            collection_name=MEMES_COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "filename": filename,
                        "description": description,
                        "group_id": group_id,
                        "manual": manual,
                    },
                )
            ],
        )
    except Exception as e:
        print(f"[Qdrant] 表情包入库失败 {filename[:12]}: {e}")


async def delete_meme(point_id: int):
    client = _get_client()
    try:
        await client.delete(
            collection_name=MEMES_COLLECTION,
            points_selector=PointIdsList(points=[point_id]),
        )
    except Exception as e:
        print(f"[Qdrant] 表情包删索引失败 {point_id}: {e}")


async def search_memes(
    group_id: str, query_vector: list[float], top_k: int = 3
) -> list[dict]:
    """检索表情包：本群 + 全局（group_id=''）"""
    client = _get_client()
    try:
        results = await client.search(
            collection_name=MEMES_COLLECTION,
            query_vector=query_vector,
            query_filter=Filter(
                must=[
                    FieldCondition(key="group_id", match=MatchAny(any=[group_id, ""]))
                ]
            ),
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "filename": r.payload.get("filename", ""),
                "description": r.payload.get("description", ""),
                "manual": r.payload.get("manual", False),
                "score": r.score,
            }
            for r in results
        ]
    except Exception as e:
        print(f"[Qdrant] 表情包检索失败: {e}")
        return []
