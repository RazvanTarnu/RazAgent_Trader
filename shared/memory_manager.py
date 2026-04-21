# -*- coding: utf-8 -*-
"""Memory Manager — V23.00

RAG-based long-term memory for RazAgent. Uses ChromaDB for vector storage
with automatic embedding (all-MiniLM-L6-v2 via chromadb default).

V23.00: Hybrid search (Vector + BM25) and full metadata where-clause filtering.

Memory types:
  - video_generation: Topics, styles, scores, success/failure
  - voice_command:    CEO voice commands and agent responses
  - system_event:     Errors, fixes, configuration changes
  - ceo_preference:   User feedback, likes/dislikes
  - learning:         Insights extracted from failures

Usage:
    from shared.memory_manager import remember, recall, get_recent_memories

    # Save a memory
    remember("Generated horror video about deep sea anomaly", category="video_generation",
             metadata={"topic": "deep sea", "style": "horror", "score": 85})

    # Retrieve relevant memories (hybrid search by default)
    context = recall("deep sea creature video", top_k=3)

    # Retrieve with metadata filtering
    context = recall("deep sea creature video", top_k=3,
                     where={"style": "horror"},
                     where_document={"$contains": "anomaly"})

    # Get last N memories for dashboard
    recent = get_recent_memories(limit=5)
"""
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path

from shared.config import DATA_DIR

logger = logging.getLogger("godclaw.memory_manager")

VECTOR_DB_PATH = str(DATA_DIR / "vector_memory")
COLLECTION_NAME = "razagent_core_memory"

_client = None
_collection = None


def _get_collection():
    """Lazy-init ChromaDB client and collection (singleton)."""
    global _client, _collection
    if _collection is not None:
        return _collection

    import os
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    _client = chromadb.PersistentClient(
        path=VECTOR_DB_PATH,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    _collection = _client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    logger.info(
        "Memory Manager initialized: %s (%d memories)",
        VECTOR_DB_PATH, _collection.count(),
    )
    return _collection


# ─────────────────────────────────────────────
# BM25 Scoring (V23.00)
# ─────────────────────────────────────────────

def _bm25_score(query: str, document: str) -> float:
    """Simple BM25-inspired keyword scoring (no external deps).

    Uses term frequency with diminishing returns (BM25 saturation).
    Returns a score in [0, 1] range.
    """
    if not query or not document:
        return 0.0
    q_terms = set(query.lower().split())
    doc_lower = document.lower()
    doc_words = doc_lower.split()
    doc_len = len(doc_words)
    if doc_len == 0:
        return 0.0

    k1 = 1.5
    b = 0.75
    avg_dl = 150  # approximate average memory doc length

    score = 0.0
    for term in q_terms:
        tf = doc_lower.count(term)
        if tf == 0:
            continue
        # BM25 TF saturation
        tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_dl))
        # Simple IDF approximation
        idf = 1.0 / len(q_terms)
        score += tf_norm * idf

    # Normalize to [0, 1]
    return min(1.0, score / max(1, len(q_terms)))


# ─────────────────────────────────────────────
# Metadata Where-Clause Builder (V23.00)
# ─────────────────────────────────────────────

def _build_where_clause(
    category: str | None = None,
    min_importance: int = 0,
    extra_where: dict | None = None,
) -> dict | None:
    """Build a ChromaDB where clause from individual filters.

    Combines multiple filters with $and. Supports all ChromaDB operators
    when passed via extra_where: $eq, $ne, $in, $nin, $gt, $gte, $lt, $lte.

    Returns:
        ChromaDB-compatible where dict, or None if no filters.
    """
    conditions: list[dict] = []

    if category:
        conditions.append({"category": category})
    if min_importance > 0:
        conditions.append({"importance": {"$gte": min_importance}})
    if extra_where:
        conditions.append(extra_where)

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def remember(
    text: str,
    category: str = "general",
    metadata: dict | None = None,
    importance: int = 5,
) -> str:
    """Store a memory in the vector database.

    Args:
        text: The memory content (will be embedded automatically).
        category: video_generation | voice_command | system_event | ceo_preference | learning
        metadata: Additional key-value pairs to store alongside.
        importance: 1-10 scale (higher = more important, affects retrieval ranking).

    Returns:
        The memory ID.
    """
    col = _get_collection()
    mem_id = f"mem_{int(time.time())}_{uuid.uuid4().hex[:6]}"

    meta = {
        "category": category,
        "importance": importance,
        "timestamp": datetime.now().isoformat(),
        "ts_unix": int(time.time()),
    }
    if metadata:
        # ChromaDB metadata values must be str, int, float, or bool
        for k, v in metadata.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
            else:
                meta[k] = str(v)

    col.add(
        documents=[text],
        metadatas=[meta],
        ids=[mem_id],
    )

    logger.info("Memory saved: [%s] %s (id=%s)", category, text[:60], mem_id)
    return mem_id


def recall(
    query: str,
    top_k: int = 3,
    category: str | None = None,
    min_importance: int = 0,
    where: dict | None = None,
    where_document: dict | None = None,
    hybrid: bool = True,
    vector_weight: float = 0.6,
    bm25_weight: float = 0.4,
) -> list[dict]:
    """Retrieve relevant memories using hybrid search (Vector + BM25).

    Combines cosine similarity from ChromaDB with BM25 keyword scoring
    for better relevance. Falls back to vector-only if hybrid=False.

    Args:
        query: Search query (will be embedded and matched against stored memories).
        top_k: Number of results to return.
        category: Filter by category (optional).
        min_importance: Filter by minimum importance level.
        where: Extra ChromaDB metadata filter dict (combined with category/importance
               via $and). Supports operators: $eq, $ne, $in, $nin, $gt, $gte, $lt, $lte.
        where_document: ChromaDB document content filter (e.g. {"$contains": "error"}).
        hybrid: If True (default), use hybrid Vector+BM25 re-ranking.
        vector_weight: Weight for vector similarity (0-1), used when hybrid=True.
        bm25_weight: Weight for BM25 keyword score (0-1), used when hybrid=True.

    Returns:
        List of dicts with keys: text, category, importance, timestamp, distance,
        [similarity, bm25_score, hybrid_score if hybrid=True].
    """
    col = _get_collection()
    if col.count() == 0:
        return []

    where_clause = _build_where_clause(
        category=category,
        min_importance=min_importance,
        extra_where=where,
    )

    fetch_n = top_k * 3 if hybrid else top_k

    try:
        query_kwargs: dict = {
            "query_texts": [query],
            "n_results": min(fetch_n, col.count()),
        }
        if where_clause:
            query_kwargs["where"] = where_clause
        if where_document:
            query_kwargs["where_document"] = where_document

        results = col.query(**query_kwargs)
    except Exception as e:
        logger.error("Memory recall failed: %s", e)
        return []

    memories = []
    if results and results["documents"] and results["documents"][0]:
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            dist = results["distances"][0][i] if results["distances"] else 1.0
            similarity = round(1.0 - dist, 4)

            memory: dict = {
                "text": doc,
                "category": meta.get("category", "general"),
                "importance": meta.get("importance", 5),
                "timestamp": meta.get("timestamp", ""),
                "distance": round(dist, 3),
                "similarity": similarity,
                "id": results["ids"][0][i] if results["ids"] else "",
                **{k: v for k, v in meta.items()
                   if k not in ("category", "importance", "timestamp", "ts_unix")},
            }

            if hybrid:
                bm25 = _bm25_score(query, doc)
                memory["bm25_score"] = round(bm25, 4)
                memory["hybrid_score"] = round(
                    similarity * vector_weight + bm25 * bm25_weight, 4
                )

            memories.append(memory)

    # Sort by hybrid_score if hybrid, else by similarity (= lowest distance)
    sort_key = "hybrid_score" if hybrid else "similarity"
    memories.sort(key=lambda m: m.get(sort_key, 0), reverse=True)
    return memories[:top_k]


def get_recent_memories(limit: int = 5, category: str | None = None) -> list[dict]:
    """Get the most recent memories (chronological, not semantic).

    Args:
        limit: Number of memories to return.
        category: Filter by category (optional).

    Returns:
        List of dicts sorted by timestamp descending.
    """
    col = _get_collection()
    if col.count() == 0:
        return []

    try:
        where_filter = {"category": category} if category else None
        results = col.get(
            where=where_filter,
            limit=min(limit * 3, col.count()),  # Fetch extra for sorting
            include=["documents", "metadatas"],
        )
    except Exception as e:
        logger.error("Recent memories fetch failed: %s", e)
        return []

    memories = []
    if results and results["documents"]:
        for i, doc in enumerate(results["documents"]):
            meta = results["metadatas"][i] if results["metadatas"] else {}
            memories.append({
                "text": doc,
                "category": meta.get("category", "general"),
                "importance": meta.get("importance", 5),
                "timestamp": meta.get("timestamp", ""),
                "id": results["ids"][i] if results["ids"] else "",
            })

    # Sort by timestamp descending
    memories.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return memories[:limit]


def format_memories_for_prompt(memories: list[dict], max_tokens: int = 300) -> str:
    """Format retrieved memories as a compact text block for LLM prompt injection.

    Keeps output under ~300 tokens to fit in qwen3:30b's 8192 context.
    """
    if not memories:
        return ""

    lines = ["RELEVANT PAST CONTEXT (from long-term memory):"]
    char_budget = max_tokens * 4  # ~4 chars per token

    for m in memories:
        line = f"- [{m['category']}] {m['text'][:120]}"
        if m.get("timestamp"):
            # Just date, not full ISO
            line += f" ({m['timestamp'][:10]})"
        if len("\n".join(lines)) + len(line) > char_budget:
            break
        lines.append(line)

    return "\n".join(lines)


def memory_stats() -> dict:
    """Get memory database statistics."""
    col = _get_collection()
    count = col.count()

    categories = {}
    if count > 0:
        try:
            results = col.get(include=["metadatas"], limit=min(count, 1000))
            for meta in (results.get("metadatas") or []):
                cat = meta.get("category", "general") if isinstance(meta, dict) else "general"
                categories[cat] = categories.get(cat, 0) + 1
        except Exception:
            pass

    return {
        "total_memories": count,
        "categories": categories,
        "db_path": VECTOR_DB_PATH,
        "collection": COLLECTION_NAME,
    }
