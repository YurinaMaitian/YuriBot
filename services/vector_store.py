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

# ========== 文档库（docs：PDF chunk + 章节摘要） ==========
DOCS_COLLECTION = "docs"


async def init_docs_collection():
    client = _get_client()
    try:
        collections = await client.get_collections()
        if DOCS_COLLECTION in [c.name for c in collections.collections]:
            print(f"[Qdrant] Collection '{DOCS_COLLECTION}' 已存在")
            return
        await client.create_collection(
            collection_name=DOCS_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        print(f"[Qdrant] Collection '{DOCS_COLLECTION}' 创建成功")
    except Exception as e:
        print(f"[Qdrant] docs 初始化失败: {e}")


async def upsert_doc_point(
    point_id: int,
    doc_id: str,
    text: str,
    page_start: int,
    page_end: int,
    section_path: str,
    kind: str,
    group_id: str,
    vector: list[float],
    idx: int = 0,
):
    client = _get_client()
    try:
        await client.upsert(
            collection_name=DOCS_COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "doc_id": doc_id,
                        "text": text[:800],
                        "page_start": page_start,
                        "page_end": page_end,
                        "section_path": section_path[:100],
                        "kind": kind,  # "chunk" | "section_summary"
                        "group_id": group_id,
                        "idx": idx,
                    },
                )
            ],
        )
    except Exception as e:
        print(f"[Qdrant] doc 入库失败: {e}")


async def delete_doc_points(doc_id: str):
    client = _get_client()
    try:
        await client.delete(
            collection_name=DOCS_COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
        )
    except Exception as e:
        print(f"[Qdrant] doc 删点失败 {doc_id[:8]}: {e}")


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


# ========== 梗百科索引 ==========

SLANG_COLLECTION = "slang"


async def init_slang_collection():
    client = _get_client()
    try:
        collections = await client.get_collections()
        if SLANG_COLLECTION in [c.name for c in collections.collections]:
            print(f"[Qdrant] Collection '{SLANG_COLLECTION}' 已存在")
            return
        await client.create_collection(
            collection_name=SLANG_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        print(f"[Qdrant] Collection '{SLANG_COLLECTION}' 创建成功")
    except Exception as e:
        print(f"[Qdrant] slang 初始化失败: {e}")


async def upsert_slang_point(
    point_id: int, term: str, explanation: str, group_id: str, vector: list[float]
):
    client = _get_client()
    try:
        await client.upsert(
            collection_name=SLANG_COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "term": term,
                        "explanation": explanation,
                        "group_id": group_id,
                    },
                )
            ],
        )
    except Exception as e:
        print(f"[Qdrant] 梗入库失败 {term}: {e}")


async def delete_slang_point(point_id: int):
    client = _get_client()
    try:
        await client.delete(
            collection_name=SLANG_COLLECTION,
            points_selector=PointIdsList(points=[point_id]),
        )
    except Exception as e:
        print(f"[Qdrant] 梗删索引失败 {point_id}: {e}")


async def search_slang_points(query_vector: list[float], top_k: int = 2) -> list[dict]:
    client = _get_client()
    try:
        results = await client.search(
            collection_name=SLANG_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "term": r.payload.get("term", ""),
                "explanation": r.payload.get("explanation", ""),
                "score": r.score,
            }
            for r in results
        ]
    except Exception as e:
        print(f"[Qdrant] 梗检索失败: {e}")
        return []


# ========== 搜索知识库（web_notes：语义缓存，覆盖写纠错） ==========
WEB_NOTES_COLLECTION = "web_notes"


async def init_web_notes_collection():
    client = _get_client()
    try:
        collections = await client.get_collections()
        if WEB_NOTES_COLLECTION in [c.name for c in collections.collections]:
            print(f"[Qdrant] Collection '{WEB_NOTES_COLLECTION}' 已存在")
            return
        await client.create_collection(
            collection_name=WEB_NOTES_COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        print(f"[Qdrant] Collection '{WEB_NOTES_COLLECTION}' 创建成功")
    except Exception as e:
        print(f"[Qdrant] web_notes 初始化失败: {e}")


async def upsert_web_note(
    point_id: int,
    query: str,
    answer: str,
    url: str,
    revision: int,
    vector: list[float],
    group_id: str = "",
):
    """point_id 复用旧值=覆盖写（纠错），新值=新记录。ID 由调用方语义查重后决定"""
    from datetime import datetime

    client = _get_client()
    try:
        await client.upsert(
            collection_name=WEB_NOTES_COLLECTION,
            points=[
                PointStruct(
                    id=point_id,
                    vector=vector,
                    payload={
                        "query": query[:100],
                        "answer": answer[:500],
                        "url": url[:200],
                        "revision": revision,
                        "group_id": group_id,
                        "created_at": datetime.now().isoformat(timespec="seconds"),
                    },
                )
            ],
        )
    except Exception as e:
        print(f"[Qdrant] web_note 入库失败: {e}")


async def search_web_notes(query_vector: list[float], top_k: int = 1) -> list[dict]:
    """返回带 point id 的命中结果（id 是覆盖写的钥匙）"""
    client = _get_client()
    try:
        results = await client.search(
            collection_name=WEB_NOTES_COLLECTION,
            query_vector=query_vector,
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "id": r.id,
                "score": r.score,
                "query": r.payload.get("query", ""),
                "answer": r.payload.get("answer", ""),
                "created_at": r.payload.get("created_at", ""),
                "revision": r.payload.get("revision", 1),
            }
            for r in results
        ]
    except Exception as e:
        print(f"[Qdrant] web_notes 检索失败: {e}")
        return []


async def search_doc_chunks(
    doc_id: str, query_vector: list[float], top_k: int = 3
) -> list[dict]:
    client = _get_client()
    try:
        results = await client.search(
            collection_name=DOCS_COLLECTION,
            query_vector=query_vector,
            query_filter=Filter(
                must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
            ),
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "text": r.payload.get("text", ""),
                "page_start": r.payload.get("page_start", 0),
                "page_end": r.payload.get("page_end", 0),
                "section_path": r.payload.get("section_path", ""),
            }
            for r in results
        ]
    except Exception as e:
        print(f"[Qdrant] docs 检索失败: {e}")
        return []
