"""V30.1 — RAG Sync Engine.
One-time ETL import from SQLite databases into ChromaDB unified vector store.
Also provides write-through helpers for real-time indexing.
"""
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

# Silence ChromaDB telemetry (posthog capture errors) before any chromadb import
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

from shared.config import DATA_DIR, CHROMA_PATH, AGENT_DB

logger = logging.getLogger("godclaw.rag_sync")

# Databases to sync
SYNC_SOURCES = [
    {
        "db": str(AGENT_DB),
        "table": "audit_log",
        "text_col": "details",
        "meta_cols": ["action", "agent_id", "timestamp"],
        "collection": "godclaw_memory",
        "prefix": "audit",
    },
    {
        "db": str(DATA_DIR / "audit_logs.db"),
        "table": "audit_actions",
        "text_col": "details",
        "meta_cols": ["action_type", "agent_id", "target", "timestamp"],
        "collection": "godclaw_memory",
        "prefix": "audit_v2",
    },
    {
        "db": str(DATA_DIR / "mission_control.db"),
        "table": "mission_tasks",
        "text_col": "title",
        "meta_cols": ["assignee", "stage", "priority", "created_at"],
        "collection": "godclaw_memory",
        "prefix": "mission",
    },
]


def _get_chroma_client():
    """Get ChromaDB PersistentClient."""
    import chromadb
    Path(CHROMA_PATH).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PATH)


def _get_embed_fn():
    """Get Ollama embedding function."""
    try:
        from chromadb.utils.embedding_functions import OllamaEmbeddingFunction
        return OllamaEmbeddingFunction(
            model_name="nomic-embed-text",
            url="http://localhost:11434",
        )
    except Exception:
        return None


def run_etl(dry_run: bool = False) -> dict:
    """One-time ETL: import semantic data from SQLite into ChromaDB.

    Args:
        dry_run: If True, just count records without importing.
    Returns:
        Summary dict with counts per source.
    """
    client = _get_chroma_client()
    embed_fn = _get_embed_fn()
    results = {"sources": [], "total_imported": 0, "dry_run": dry_run}

    for src in SYNC_SOURCES:
        db_path = src["db"]
        if not Path(db_path).exists():
            results["sources"].append({"db": db_path, "status": "not_found", "count": 0})
            continue

        try:
            conn = sqlite3.connect(db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")

            # Check if table exists
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if src["table"] not in tables:
                results["sources"].append({"db": db_path, "table": src["table"], "status": "table_missing", "count": 0})
                conn.close()
                continue

            rows = conn.execute(f"SELECT * FROM {src['table']}").fetchall()
            conn.close()

            if dry_run:
                results["sources"].append({"db": db_path, "table": src["table"], "status": "dry_run", "count": len(rows)})
                results["total_imported"] += len(rows)
                continue

            # Get or create collection
            col = client.get_or_create_collection(
                name=src["collection"],
                metadata={"hnsw:space": "cosine"},
                embedding_function=embed_fn,
            )

            imported = 0
            batch_docs, batch_metas, batch_ids = [], [], []

            for row in rows:
                row_dict = dict(row)
                text = str(row_dict.get(src["text_col"], ""))
                if not text or len(text.strip()) < 5:
                    continue

                doc_id = f"{src['prefix']}_{row_dict.get('id', int(time.time()*1000))}_{imported}"
                meta = {"source_db": db_path, "source_table": src["table"]}
                for mc in src["meta_cols"]:
                    val = row_dict.get(mc, "")
                    if val is not None:
                        meta[mc] = str(val)[:500]

                batch_docs.append(text[:2000])
                batch_metas.append(meta)
                batch_ids.append(doc_id)
                imported += 1

                # Batch insert every 50
                if len(batch_docs) >= 50:
                    try:
                        col.add(documents=batch_docs, metadatas=batch_metas, ids=batch_ids)
                    except Exception as e:
                        logger.warning(f"Batch insert failed: {e}")
                    batch_docs, batch_metas, batch_ids = [], [], []

            # Final batch
            if batch_docs:
                try:
                    col.add(documents=batch_docs, metadatas=batch_metas, ids=batch_ids)
                except Exception as e:
                    logger.warning(f"Final batch insert failed: {e}")

            results["sources"].append({"db": db_path, "table": src["table"], "status": "imported", "count": imported})
            results["total_imported"] += imported

        except Exception as e:
            results["sources"].append({"db": db_path, "status": "error", "error": str(e), "count": 0})

    logger.info(f"RAG ETL complete: {results['total_imported']} documents imported from {len(results['sources'])} sources")
    return results


def write_through(text: str, metadata: dict, collection_name: str = "godclaw_memory") -> bool:
    """Write-through: index a single document into ChromaDB immediately.
    Call this from agents after writing to SQLite.
    """
    if not text or len(text.strip()) < 5:
        return False
    try:
        client = _get_chroma_client()
        embed_fn = _get_embed_fn()
        col = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=embed_fn,
        )
        doc_id = f"wt_{int(time.time()*1000)}"
        col.add(documents=[text[:2000]], metadatas=[metadata], ids=[doc_id])
        return True
    except Exception as e:
        logger.warning(f"Write-through failed: {e}")
        return False


def semantic_search(
    query: str,
    limit: int = 5,
    collection_name: str = "godclaw_memory",
    where: dict | None = None,
    where_document: dict | None = None,
) -> list[dict]:
    """Search ChromaDB semantically using nomic-embed-text.

    Args:
        query: Natural language search query.
        limit: Max results.
        collection_name: ChromaDB collection name.
        where: Metadata filter dict (e.g. {"source_table": "audit_actions"}).
               Supports ChromaDB operators: $eq, $ne, $in, $nin, $gt, $gte, $lt, $lte, $and, $or.
        where_document: Document content filter (e.g. {"$contains": "error"}).
    """
    try:
        client = _get_chroma_client()
        embed_fn = _get_embed_fn()
        col = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
            embedding_function=embed_fn,
        )
        query_kwargs: dict = {
            "query_texts": [query],
            "n_results": min(limit, 20),
        }
        if where:
            query_kwargs["where"] = where
        if where_document:
            query_kwargs["where_document"] = where_document

        results = col.query(**query_kwargs)
        hits = []
        if results["documents"]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                hits.append({
                    "content": doc,
                    "metadata": meta,
                    "similarity": round(1 - dist, 4),
                })
        return hits
    except Exception as e:
        logger.warning(f"Semantic search failed: {e}")
        return []


def _bm25_score(query: str, document: str) -> float:
    """Simple BM25-inspired keyword scoring (no external deps).

    Uses term frequency with diminishing returns and IDF approximation.
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
    avg_dl = 200  # approximate average doc length

    score = 0.0
    for term in q_terms:
        tf = doc_lower.count(term)
        if tf == 0:
            continue
        # BM25 TF saturation
        tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc_len / avg_dl))
        # Simple IDF approximation (fewer matches in query = higher weight)
        idf = 1.0 / len(q_terms)
        score += tf_norm * idf

    # Normalize to [0, 1]
    return min(1.0, score / max(1, len(q_terms)))


def hybrid_search(
    query: str,
    limit: int = 5,
    collection_name: str = "godclaw_memory",
    where: dict | None = None,
    where_document: dict | None = None,
    vector_weight: float = 0.6,
    bm25_weight: float = 0.4,
) -> list[dict]:
    """Hybrid search combining vector similarity + BM25 keyword scoring.

    Fetches 3x limit from vector search, then re-ranks using weighted
    combination of cosine similarity and BM25 keyword score.

    Args:
        query: Natural language search query.
        limit: Max results returned.
        collection_name: ChromaDB collection name.
        where: Metadata filter (ChromaDB where clause).
        where_document: Document content filter.
        vector_weight: Weight for vector similarity score (0-1).
        bm25_weight: Weight for BM25 keyword score (0-1).

    Returns:
        List of {content, metadata, similarity, bm25_score, hybrid_score}.
    """
    # Fetch more candidates for re-ranking
    candidates = semantic_search(
        query=query,
        limit=limit * 3,
        collection_name=collection_name,
        where=where,
        where_document=where_document,
    )

    if not candidates:
        return []

    # Re-rank with hybrid scoring
    for hit in candidates:
        bm25 = _bm25_score(query, hit["content"])
        hit["bm25_score"] = round(bm25, 4)
        hit["hybrid_score"] = round(
            hit["similarity"] * vector_weight + bm25 * bm25_weight, 4
        )

    candidates.sort(key=lambda x: x["hybrid_score"], reverse=True)
    return candidates[:limit]
