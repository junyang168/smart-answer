from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from backend.api.config import DATA_BASE_PATH

from .bible_refs import extract_refs, normalize_ref, refs_overlap
from .discovery import discover_manuscripts
from .embedding_client import EmbeddingClient
from .markdown_parser import parse_manuscript
from .models import (
    CanonicalRef,
    DiscoveredManuscript,
    IndexStatus,
    ReindexResponse,
    SearchFilters,
    SourceCard,
    SourceUnit,
)
from .topics import expand_topic_query, extract_topics, load_topic_definitions, topic_taxonomy_path


def _dump_model(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _default_db_path() -> Path:
    configured = os.getenv("SERMON_SEARCH_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DATA_BASE_PATH / "sermon_search" / "sermon_search.sqlite3"


@dataclass
class Candidate:
    source_id: str
    score: float = 0.0
    tools: set[str] = field(default_factory=set)

    def add(self, score: float, tool: str) -> None:
        self.score += score
        self.tools.add(tool)


class SermonSearchIndex:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _default_db_path()
        self.embedding_client = EmbeddingClient()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS index_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    series_id TEXT NOT NULL,
                    series_title TEXT NOT NULL,
                    lecture_id TEXT NOT NULL,
                    lecture_title TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    project_title TEXT NOT NULL,
                    project_type TEXT NOT NULL,
                    bible_verse TEXT,
                    manuscript_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    modified_time REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_units (
                    source_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    series_id TEXT NOT NULL,
                    series_title TEXT NOT NULL,
                    lecture_id TEXT NOT NULL,
                    lecture_title TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    project_title TEXT NOT NULL,
                    heading_path_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    topic_tags_json TEXT NOT NULL,
                    content_types_json TEXT NOT NULL,
                    terms_json TEXT NOT NULL,
                    refs_json TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                );

                CREATE TABLE IF NOT EXISTS source_unit_refs (
                    source_id TEXT NOT NULL,
                    osis TEXT NOT NULL,
                    raw TEXT NOT NULL,
                    book TEXT NOT NULL,
                    book_zh TEXT,
                    chapter_start INTEGER NOT NULL,
                    verse_start INTEGER,
                    chapter_end INTEGER,
                    verse_end INTEGER,
                    role TEXT NOT NULL,
                    PRIMARY KEY(source_id, osis, role)
                );

                CREATE TABLE IF NOT EXISTS document_refs (
                    document_id TEXT NOT NULL,
                    osis TEXT NOT NULL,
                    raw TEXT NOT NULL,
                    book TEXT NOT NULL,
                    book_zh TEXT,
                    chapter_start INTEGER NOT NULL,
                    verse_start INTEGER,
                    chapter_end INTEGER,
                    verse_end INTEGER,
                    PRIMARY KEY(document_id, osis)
                );

                CREATE TABLE IF NOT EXISTS source_unit_topics (
                    source_id TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    PRIMARY KEY(source_id, topic)
                );

                CREATE TABLE IF NOT EXISTS source_unit_embeddings (
                    source_id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    vector_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_documents_series ON documents(series_id, project_type);
                CREATE INDEX IF NOT EXISTS idx_source_units_doc ON source_units(document_id);
                CREATE INDEX IF NOT EXISTS idx_refs_lookup ON source_unit_refs(book, chapter_start, role);
                CREATE INDEX IF NOT EXISTS idx_document_refs_doc ON document_refs(document_id);
                CREATE INDEX IF NOT EXISTS idx_topics_lookup ON source_unit_topics(topic);
                """
            )
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS source_units_fts
                    USING fts5(source_id UNINDEXED, search_text, tokenize='unicode61');
                    """
                )
            except sqlite3.OperationalError:
                pass

    def status(self) -> IndexStatus:
        self.initialize()
        with self.connect() as conn:
            document_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            unit_count = conn.execute("SELECT COUNT(*) FROM source_units").fetchone()[0]
            embedding_count = conn.execute("SELECT COUNT(*) FROM source_unit_embeddings").fetchone()[0]
            row = conn.execute(
                "SELECT value FROM index_metadata WHERE key = 'indexed_at'"
            ).fetchone()
        return IndexStatus(
            db_path=str(self.db_path),
            document_count=document_count,
            source_unit_count=unit_count,
            indexed_at=row[0] if row else None,
            embedding_enabled=embedding_count > 0,
        )

    def list_documents(self) -> List[dict]:
        self.initialize()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT document_id, series_title, lecture_title, project_id,
                       project_title, project_type
                FROM documents
                ORDER BY series_title, lecture_title, project_title
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def find_documents(self, query: str, limit: int = 10) -> List[dict]:
        self.initialize()
        normalized_query = self._normalize_title(query)
        query_terms = [self._normalize_title(term) for term in self._query_terms(query)]
        query_terms = [term for term in query_terms if term]
        scored: List[dict] = []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT document_id, series_title, lecture_title, project_id,
                       project_title, project_type, bible_verse
                FROM documents
                """
            ).fetchall()
        for row in rows:
            doc = dict(row)
            haystack = self._normalize_title(
                " ".join(
                    [
                        doc.get("project_id") or "",
                        doc.get("project_title") or "",
                        doc.get("lecture_title") or "",
                        doc.get("bible_verse") or "",
                    ]
                )
            )
            score = 0.0
            if normalized_query:
                if normalized_query == haystack:
                    score += 100.0
                elif normalized_query in haystack:
                    score += 80.0
                elif haystack in normalized_query:
                    score += 65.0
            for term in query_terms:
                if term and term in haystack:
                    score += min(30.0, 4.0 + len(term))
            if score:
                doc["score"] = round(score, 3)
                scored.append(doc)
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    def rebuild(
        self,
        series_ids: Optional[Iterable[str]] = None,
        project_types: Optional[Iterable[str]] = None,
        include_embeddings: bool = False,
    ) -> ReindexResponse:
        manuscripts = discover_manuscripts(series_ids=series_ids, project_types=project_types)
        return self.rebuild_from_manuscripts(manuscripts, include_embeddings=include_embeddings)

    def rebuild_from_manuscripts(
        self,
        manuscripts: Sequence[DiscoveredManuscript],
        include_embeddings: bool = False,
    ) -> ReindexResponse:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.db_path.with_name(f".{self.db_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        if tmp_path.exists():
            tmp_path.unlink()
        tmp_index = SermonSearchIndex(tmp_path)
        try:
            response = tmp_index._populate_from_manuscripts(manuscripts, include_embeddings=include_embeddings)
            if manuscripts and response.documents_indexed == 0:
                self._cleanup_temp_db(tmp_path)
                return ReindexResponse(
                    status="failed",
                    documents_indexed=0,
                    source_units_indexed=0,
                    skipped=[
                        *response.skipped,
                        {"reason": "new index had no valid documents; existing index was left unchanged"},
                    ],
                )
            os.replace(tmp_path, self.db_path)
            self._cleanup_temp_db(tmp_path)
            return response
        except Exception:
            self._cleanup_temp_db(tmp_path)
            raise

    def _populate_from_manuscripts(
        self,
        manuscripts: Sequence[DiscoveredManuscript],
        include_embeddings: bool = False,
    ) -> ReindexResponse:
        self.initialize()
        skipped: List[dict] = []
        indexed_documents = 0
        indexed_units = 0
        embeddings_enabled = include_embeddings and self.embedding_client.available
        if include_embeddings and not embeddings_enabled:
            skipped.append({"reason": "SERMON_SEARCH_EMBEDDING_PROVIDER is not configured; embeddings skipped"})
        with self.connect() as conn:
            conn.execute("DELETE FROM source_unit_refs")
            conn.execute("DELETE FROM document_refs")
            conn.execute("DELETE FROM source_unit_topics")
            conn.execute("DELETE FROM source_unit_embeddings")
            conn.execute("DELETE FROM source_units")
            conn.execute("DELETE FROM documents")
            try:
                conn.execute("DELETE FROM source_units_fts")
            except sqlite3.OperationalError:
                pass

            for manuscript in manuscripts:
                try:
                    markdown = manuscript.manuscript_path.read_text(encoding="utf-8")
                    units = parse_manuscript(manuscript, markdown)
                except Exception as exc:
                    skipped.append({"project_id": manuscript.project_id, "reason": str(exc)})
                    continue
                if not units:
                    skipped.append({"project_id": manuscript.project_id, "reason": "no source units"})
                    continue

                document_id = units[0].document_id
                conn.execute(
                    """
                    INSERT INTO documents (
                        document_id, series_id, series_title, lecture_id, lecture_title,
                        project_id, project_title, project_type, bible_verse,
                        manuscript_path, content_hash, modified_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        manuscript.series_id,
                        manuscript.series_title,
                        manuscript.lecture_id,
                        manuscript.lecture_title,
                        manuscript.project_id,
                        manuscript.project_title,
                        manuscript.project_type,
                        manuscript.bible_verse,
                        str(manuscript.manuscript_path),
                        manuscript.content_hash,
                        manuscript.modified_time,
                    ),
                )
                self._insert_document_refs(conn, document_id, units[0].document_scope_refs)
                for unit in units:
                    self._insert_unit(conn, unit)
                if embeddings_enabled:
                    self._insert_embeddings(conn, units, skipped)
                indexed_documents += 1
                indexed_units += len(units)

            conn.execute(
                "INSERT OR REPLACE INTO index_metadata(key, value) VALUES('indexed_at', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
        return ReindexResponse(
            status="ok",
            documents_indexed=indexed_documents,
            source_units_indexed=indexed_units,
            skipped=skipped,
        )

    def _cleanup_temp_db(self, path: Path) -> None:
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"), Path(f"{path}-journal")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass

    def _insert_document_refs(
        self,
        conn: sqlite3.Connection,
        document_id: str,
        refs: Sequence[CanonicalRef],
    ) -> None:
        for ref in refs:
            conn.execute(
                """
                INSERT OR IGNORE INTO document_refs (
                    document_id, osis, raw, book, book_zh, chapter_start,
                    verse_start, chapter_end, verse_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    ref.osis,
                    ref.raw,
                    ref.book,
                    ref.book_zh,
                    ref.chapter_start,
                    ref.verse_start,
                    ref.chapter_end,
                    ref.verse_end,
                ),
            )

    def _insert_unit(self, conn: sqlite3.Connection, unit: SourceUnit) -> None:
        refs_payload = [_dump_model(ref) for ref in unit.all_canonical_refs]
        conn.execute(
            """
            INSERT INTO source_units (
                source_id, document_id, series_id, series_title, lecture_id,
                lecture_title, project_id, project_title, heading_path_json,
                text, topic_tags_json, content_types_json, terms_json, refs_json, ordinal
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unit.source_id,
                unit.document_id,
                unit.series_id,
                unit.series_title,
                unit.lecture_id,
                unit.lecture_title,
                unit.project_id,
                unit.project_title,
                json.dumps(unit.heading_path, ensure_ascii=False),
                unit.text,
                json.dumps(unit.topic_tags, ensure_ascii=False),
                json.dumps(unit.content_types, ensure_ascii=False),
                json.dumps(unit.terms, ensure_ascii=False),
                json.dumps(refs_payload, ensure_ascii=False),
                unit.ordinal,
            ),
        )
        for role, refs in (
            ("primary", unit.primary_passage_refs),
            ("cross", unit.cross_refs),
            ("mention", unit.all_canonical_refs),
        ):
            for ref in refs:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO source_unit_refs (
                        source_id, osis, raw, book, book_zh, chapter_start,
                        verse_start, chapter_end, verse_end, role
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        unit.source_id,
                        ref.osis,
                        ref.raw,
                        ref.book,
                        ref.book_zh,
                        ref.chapter_start,
                        ref.verse_start,
                        ref.chapter_end,
                        ref.verse_end,
                        role,
                    ),
                )
        for topic in unit.topic_tags:
            conn.execute(
                "INSERT OR IGNORE INTO source_unit_topics(source_id, topic) VALUES(?, ?)",
                (unit.source_id, topic),
            )
        search_text = "\n".join(
            [
                unit.series_title,
                unit.lecture_title,
                unit.project_title,
                " > ".join(unit.heading_path),
                " ".join(unit.topic_tags),
                " ".join(ref.osis for ref in unit.all_canonical_refs),
                unit.text,
            ]
        )
        try:
            conn.execute(
                "INSERT INTO source_units_fts(source_id, search_text) VALUES(?, ?)",
                (unit.source_id, search_text),
            )
        except sqlite3.OperationalError:
            pass

    def _insert_embeddings(self, conn: sqlite3.Connection, units: Sequence[SourceUnit], skipped: List[dict]) -> None:
        if not self.embedding_client.available:
            skipped.append({"reason": "SERMON_SEARCH_EMBEDDING_PROVIDER is not configured; embeddings skipped"})
            return
        texts = [self._embedding_text(unit) for unit in units]
        try:
            vectors = self.embedding_client.embed_documents(texts)
        except Exception as exc:
            skipped.append({"reason": f"embedding generation failed: {exc}"})
            return
        for unit, vector in zip(units, vectors):
            conn.execute(
                """
                INSERT OR REPLACE INTO source_unit_embeddings(
                    source_id, provider, model, dimensions, vector_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    unit.source_id,
                    self.embedding_client.provider,
                    self.embedding_client.model,
                    len(vector),
                    json.dumps(vector),
                ),
            )

    def _embedding_text(self, unit: SourceUnit) -> str:
        return "\n".join(
            [
                unit.series_title,
                unit.lecture_title,
                unit.project_title,
                " > ".join(unit.heading_path),
                " ".join(unit.topic_tags),
                " ".join(ref.osis for ref in [*unit.document_scope_refs, *unit.all_canonical_refs]),
                unit.text,
            ]
        )

    def search(self, query: str, filters: Optional[SearchFilters] = None, limit: int = 50) -> Tuple[List[SourceCard], List[str]]:
        self.initialize()
        filters = filters or SearchFilters()
        candidates: Dict[str, Candidate] = {}
        tools_used: set[str] = set()
        query_refs = self._query_refs(query, filters)
        query_topics = self._query_topics(query, filters)

        def add(source_id: str, score: float, tool: str) -> None:
            candidate = candidates.setdefault(source_id, Candidate(source_id=source_id))
            candidate.add(score, tool)
            tools_used.add(tool)

        with self.connect() as conn:
            for ref in query_refs:
                for row in conn.execute(
                    """
                    SELECT source_id, osis, raw, book, book_zh, chapter_start,
                           verse_start, chapter_end, verse_end, role
                    FROM source_unit_refs
                    WHERE book = ?
                    """,
                    (ref.book,),
                ):
                    stored = CanonicalRef(
                        raw=row["raw"],
                        book=row["book"],
                        book_zh=row["book_zh"],
                        chapter_start=row["chapter_start"],
                        verse_start=row["verse_start"],
                        chapter_end=row["chapter_end"],
                        verse_end=row["verse_end"],
                        osis=row["osis"],
                    )
                    if refs_overlap(ref, stored):
                        role_weight = {"primary": 120.0, "cross": 95.0, "mention": 70.0}.get(row["role"], 50.0)
                        add(row["source_id"], role_weight, "canonical_ref")

            for topic in query_topics:
                for row in conn.execute(
                    "SELECT source_id FROM source_unit_topics WHERE topic = ?",
                    (topic,),
                ):
                    add(row["source_id"], 55.0, "topic")

            for source_id, score in self._full_text_candidates(conn, query).items():
                add(source_id, score, "full_text")

            for source_id, score in self._vector_candidates(conn, query).items():
                add(source_id, score, "semantic_vector")

            cards = self._load_cards(conn, candidates, filters, limit)

        return cards, sorted(tools_used)

    def _query_refs(self, query: str, filters: SearchFilters) -> List[CanonicalRef]:
        refs = extract_refs(query)
        for raw in filters.canonical_refs:
            ref = normalize_ref(raw)
            if ref:
                refs.append(ref)
        seen: set[str] = set()
        out: List[CanonicalRef] = []
        for ref in refs:
            if ref.osis not in seen:
                out.append(ref)
                seen.add(ref.osis)
        return out

    def _query_topics(self, query: str, filters: SearchFilters) -> List[str]:
        topics = set(extract_topics([query, " ".join(filters.topics)]))
        topics.update(t for t in filters.topics if t)
        expanded = expand_topic_query(topics)
        canonical = set(extract_topics(expanded))
        canonical.update(t for t in topics if t in canonical)
        return sorted(canonical or topics)

    def _full_text_candidates(self, conn: sqlite3.Connection, query: str) -> Dict[str, float]:
        scores: Dict[str, float] = defaultdict(float)
        terms = self._query_terms(query)
        if not terms:
            return scores

        for term in terms[:8]:
            like = f"%{term}%"
            for row in conn.execute(
                """
                SELECT source_id FROM source_units
                WHERE text LIKE ? OR project_title LIKE ? OR heading_path_json LIKE ?
                LIMIT 100
                """,
                (like, like, like),
            ):
                scores[row["source_id"]] += 12.0

        match_query = " OR ".join(self._escape_fts_term(term) for term in terms[:6] if self._escape_fts_term(term))
        if match_query:
            try:
                for row in conn.execute(
                    """
                    SELECT source_id, bm25(source_units_fts) AS rank
                    FROM source_units_fts
                    WHERE source_units_fts MATCH ?
                    ORDER BY rank
                    LIMIT 100
                    """,
                    (match_query,),
                ):
                    scores[row["source_id"]] += max(0.0, 30.0 - abs(float(row["rank"])))
            except sqlite3.OperationalError:
                pass
        return dict(scores)

    def _vector_candidates(self, conn: sqlite3.Connection, query: str) -> Dict[str, float]:
        if not self.embedding_client.available:
            return {}
        embedding_count = conn.execute("SELECT COUNT(*) FROM source_unit_embeddings").fetchone()[0]
        if not embedding_count:
            return {}
        try:
            query_vector = self.embedding_client.embed_query(query)
        except Exception:
            return {}
        if not query_vector:
            return {}
        scores: Dict[str, float] = {}
        rows = conn.execute("SELECT source_id, vector_json FROM source_unit_embeddings").fetchall()
        for row in rows:
            try:
                vector = json.loads(row["vector_json"])
            except json.JSONDecodeError:
                continue
            similarity = self._cosine_similarity(query_vector, vector)
            if similarity > 0:
                scores[row["source_id"]] = similarity * 85.0
        return dict(sorted(scores.items(), key=lambda item: item[1], reverse=True)[:120])

    def _cosine_similarity(self, a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if not norm_a or not norm_b:
            return 0.0
        return dot / (norm_a * norm_b)

    def _query_terms(self, query: str) -> List[str]:
        refs = [ref.raw for ref in extract_refs(query)]
        cleaned = re.sub(r"[？?，,。；;：:！!\(\)（）「」『』\"']", " ", query)
        base_terms = re.findall(r"[\u4e00-\u9fff]{2,}|[\u0370-\u03ff]{2,}|[\u0590-\u05ff]{2,}|[A-Za-z0-9_-]{2,}", cleaned)
        terms = refs + base_terms
        for term in base_terms:
            if re.fullmatch(r"[\u4e00-\u9fff]{5,}", term):
                stripped = re.sub(r"^(什麼是|什么是|請問|请问|何謂|何谓|怎麼看|怎么看)", "", term)
                if stripped and stripped != term:
                    terms.append(stripped)
                terms.extend(self._cjk_keyphrases(stripped or term))
        seen: set[str] = set()
        out: List[str] = []
        for term in terms:
            if term not in seen:
                out.append(term)
                seen.add(term)
        return out

    def _cjk_keyphrases(self, text: str) -> List[str]:
        protected: List[str] = []
        for definition in load_topic_definitions(str(topic_taxonomy_path())):
            protected.extend([definition.label, *definition.aliases])
        phrases = [phrase for phrase in protected if phrase in text]
        compact = re.sub(r"[的是和與及並而在有了]", " ", text)
        phrases.extend(part for part in compact.split() if len(part) >= 2)
        if len(text) <= 12:
            phrases.extend(text[i : i + 2] for i in range(0, max(0, len(text) - 1)))
            phrases.extend(text[i : i + 3] for i in range(0, max(0, len(text) - 2)))
        return phrases

    def _escape_fts_term(self, term: str) -> str:
        if not re.match(r"^[\w\u4e00-\u9fff\u0370-\u03ff\u0590-\u05ff.-]+$", term):
            return ""
        return f'"{term}"'

    def _load_cards(
        self,
        conn: sqlite3.Connection,
        candidates: Dict[str, Candidate],
        filters: SearchFilters,
        limit: int,
    ) -> List[SourceCard]:
        if not candidates:
            return []
        ordered = sorted(candidates.values(), key=lambda c: c.score, reverse=True)
        cards: List[SourceCard] = []
        for candidate in ordered:
            row = conn.execute(
                "SELECT * FROM source_units WHERE source_id = ?",
                (candidate.source_id,),
            ).fetchone()
            if not row or not self._row_passes_filters(conn, row, filters):
                continue
            headings = json.loads(row["heading_path_json"])
            topics = json.loads(row["topic_tags_json"])
            refs = json.loads(row["refs_json"])
            cards.append(
                SourceCard(
                    source_id=row["source_id"],
                    content_id=row["project_id"],
                    score=round(candidate.score, 3),
                    doc_title=row["project_title"],
                    series_title=row["series_title"],
                    lecture_title=row["lecture_title"],
                    heading_path=headings,
                    snippet=self._snippet(row["text"]),
                    topics=topics,
                    canonical_refs=[ref["osis"] for ref in refs],
                )
            )
            if len(cards) >= limit:
                break
        return cards

    def _row_passes_filters(self, conn: sqlite3.Connection, row: sqlite3.Row, filters: SearchFilters) -> bool:
        if filters.series_ids and row["series_id"] not in filters.series_ids:
            return False
        if filters.project_types:
            doc = self._document_project_type(conn, row["document_id"])
            if doc not in filters.project_types:
                return False
        content_types = set(json.loads(row["content_types_json"]))
        if filters.content_types and not content_types.intersection(filters.content_types):
            return False
        if filters.topics:
            topics = set(json.loads(row["topic_tags_json"]))
            if not topics.intersection(extract_topics(filters.topics) or filters.topics):
                return False
        return True

    def _document_project_type(self, conn: sqlite3.Connection, document_id: str) -> str:
        row = conn.execute(
            "SELECT project_type FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return row["project_type"] if row else ""

    def _snippet(self, text: str, limit: int = 260) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "…"

    def load_units(self, source_ids: Sequence[str]) -> List[SourceUnit]:
        if not source_ids:
            return []
        self.initialize()
        units: List[SourceUnit] = []
        with self.connect() as conn:
            for source_id in source_ids:
                row = conn.execute(
                    "SELECT * FROM source_units WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
                if not row:
                    continue
                refs = [CanonicalRef(**payload) for payload in json.loads(row["refs_json"])]
                role_refs = self._load_unit_role_refs(conn, source_id)
                units.append(
                    SourceUnit(
                        source_id=row["source_id"],
                        document_id=row["document_id"],
                        series_id=row["series_id"],
                        series_title=row["series_title"],
                        lecture_id=row["lecture_id"],
                        lecture_title=row["lecture_title"],
                        project_id=row["project_id"],
                        project_title=row["project_title"],
                        heading_path=json.loads(row["heading_path_json"]),
                        text=row["text"],
                        primary_passage_refs=role_refs.get("primary", []),
                        cross_refs=role_refs.get("cross", []),
                        all_canonical_refs=refs,
                        document_scope_refs=self._load_document_refs(conn, row["document_id"]),
                        topic_tags=json.loads(row["topic_tags_json"]),
                        content_types=json.loads(row["content_types_json"]),
                        terms=json.loads(row["terms_json"]),
                        ordinal=row["ordinal"],
                    )
                )
        return units

    def _load_unit_role_refs(self, conn: sqlite3.Connection, source_id: str) -> Dict[str, List[CanonicalRef]]:
        roles: Dict[str, List[CanonicalRef]] = defaultdict(list)
        rows = conn.execute(
            """
            SELECT osis, raw, book, book_zh, chapter_start, verse_start,
                   chapter_end, verse_end, role
            FROM source_unit_refs
            WHERE source_id = ?
            ORDER BY role, osis
            """,
            (source_id,),
        ).fetchall()
        for row in rows:
            if row["role"] == "mention":
                continue
            roles[row["role"]].append(
                CanonicalRef(
                    raw=row["raw"],
                    book=row["book"],
                    book_zh=row["book_zh"],
                    chapter_start=row["chapter_start"],
                    verse_start=row["verse_start"],
                    chapter_end=row["chapter_end"],
                    verse_end=row["verse_end"],
                    osis=row["osis"],
                )
            )
        return roles

    def _load_document_refs(self, conn: sqlite3.Connection, document_id: str) -> List[CanonicalRef]:
        rows = conn.execute(
            """
            SELECT osis, raw, book, book_zh, chapter_start, verse_start,
                   chapter_end, verse_end
            FROM document_refs
            WHERE document_id = ?
            ORDER BY book, chapter_start, verse_start
            """,
            (document_id,),
        ).fetchall()
        return [
            CanonicalRef(
                raw=row["raw"],
                book=row["book"],
                book_zh=row["book_zh"],
                chapter_start=row["chapter_start"],
                verse_start=row["verse_start"],
                chapter_end=row["chapter_end"],
                verse_end=row["verse_end"],
                osis=row["osis"],
            )
            for row in rows
        ]

    def find_document_units(self, document_query: str) -> List[SourceUnit]:
        self.initialize()
        matches = self.find_documents(document_query, limit=1)
        if not matches:
            return []
        document_id = matches[0]["document_id"]
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT source_id FROM source_units
                WHERE document_id = ?
                ORDER BY ordinal
                """,
                (document_id,),
            ).fetchall()
        return self.load_units([row["source_id"] for row in rows])

    def _normalize_title(self, text: str) -> str:
        return re.sub(r"[\s_\\\-－—、，,。:：]+", "", text or "").lower()
