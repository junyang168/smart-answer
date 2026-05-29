from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.api.sermon_search.bible_refs import extract_refs, normalize_ref, refs_overlap
from backend.api.sermon_search.index_store import SermonSearchIndex
from backend.api.sermon_search.markdown_parser import parse_manuscript
from backend.api.sermon_search.models import DiscoveredManuscript, SermonSearchRequest
from backend.api.sermon_search.service import SermonSearchService


class FakeLLM:
    available = True

    def __init__(self) -> None:
        self.planner_calls = 0
        self.answer_calls = 0

    def generate_json(self, messages, mode="normal"):
        if "agentic search planner" in messages[0]["content"]:
            self.planner_calls += 1
            return {"searches": [{"tool": "multi_index_search", "query": "planner should not run"}]}
        self.answer_calls += 1
        return {"answer": "根據講稿，這是快速回答。", "citations": [], "related_questions": []}


def _manuscript(path: Path, project_id: str = "project-1", title: str = "1章-耶穌的來歷", bible_verse: str | None = None) -> DiscoveredManuscript:
    return DiscoveredManuscript(
        series_id="series-1",
        series_title="馬太福音釋經",
        lecture_id="lecture-1",
        lecture_title="神的兒子，耶穌基督",
        project_id=project_id,
        project_title=title,
        project_type="sermon_note",
        bible_verse=bible_verse,
        manuscript_path=path,
        content_hash="hash",
        modified_time=1.0,
    )


class SermonSearchTests(unittest.TestCase):
    def test_extract_refs_handles_whole_bible_chinese_ranges(self):
        refs = extract_refs("太 1:22–23 引用賽 7:14，也可參照以賽亞書 54:5–6。")
        self.assertEqual([ref.osis for ref in refs], ["Matt.1.22-Matt.1.23", "Isa.7.14", "Isa.54.5-Isa.54.6"])

    def test_normalize_ref_accepts_internal_osis_refs(self):
        ref = normalize_ref("Isa.54.5-Isa.54.6")
        self.assertIsNotNone(ref)
        self.assertEqual(ref.osis, "Isa.54.5-Isa.54.6")

    def test_ref_overlap_handles_cross_chapter_ranges(self):
        broad = normalize_ref("太 16:20-17:5")
        inside = normalize_ref("太 17:1")
        outside = normalize_ref("太 18:1")
        self.assertIsNotNone(broad)
        self.assertIsNotNone(inside)
        self.assertIsNotNone(outside)
        self.assertTrue(refs_overlap(broad, inside))
        self.assertFalse(refs_overlap(broad, outside))

    def test_parser_allows_topic_only_units_without_passage_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "final.md"
            path.write_text(
                "## 聖經無誤與作者意圖\n\n"
                "這段討論聖經無誤性，重點是作者意圖，不直接綁定某一節經文。",
                encoding="utf-8",
            )
            units = parse_manuscript(_manuscript(path), path.read_text(encoding="utf-8"))

        self.assertEqual(len(units), 1)
        self.assertEqual(units[0].all_canonical_refs, [])
        self.assertIn("聖經無誤", units[0].topic_tags)
        self.assertIn("聖經無誤與作者意圖", units[0].topic_tags)

    def test_document_scope_refs_do_not_become_chunk_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "final.md"
            path.write_text(
                "## 神學主題\n\n"
                "這一段只討論門徒訓練，沒有直接引用經節。",
                encoding="utf-8",
            )
            units = parse_manuscript(
                _manuscript(path, title="16章釋經", bible_verse="太 16:1-19"),
                path.read_text(encoding="utf-8"),
            )

        self.assertEqual(units[0].all_canonical_refs, [])
        self.assertEqual([ref.osis for ref in units[0].document_scope_refs], ["Matt.16.1-Matt.16.19"])

    def test_index_searches_cross_refs_and_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscript_path = root / "final.md"
            manuscript_path.write_text(
                "## 新郎比喻與神和子民的關係\n\n"
                "以賽亞書 54:5–6 用婚姻意象表達神與子民的關係。"
                "耶穌在太 9:15 使用新郎稱謂，承接這個舊約神學傳統。",
                encoding="utf-8",
            )
            index = SermonSearchIndex(root / "search.sqlite3")
            response = index.rebuild_from_manuscripts([_manuscript(manuscript_path)])
            cards, tools = index.search("以賽亞書 54:5-6 新郎", limit=5)

        self.assertEqual(response.source_units_indexed, 1)
        self.assertTrue(cards)
        self.assertIn("canonical_ref", tools)
        self.assertIn("舊約應驗", cards[0].topics)

    def test_document_lookup_scales_past_first_eighty_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manuscripts = []
            for index in range(120):
                path = root / f"doc-{index}.md"
                path.write_text(f"## 第 {index} 段\n\n太 {index + 1}:1 的講稿內容。", encoding="utf-8")
                manuscripts.append(
                    _manuscript(
                        path,
                        project_id=f"project-{index}",
                        title=f"{index}章釋經",
                        bible_verse=f"太 {index + 1}:1",
                    )
                )
            search_index = SermonSearchIndex(root / "search.sqlite3")
            search_index.rebuild_from_manuscripts(manuscripts)

            docs = search_index.find_documents("119章釋經", limit=3)

        self.assertTrue(docs)
        self.assertEqual(docs[0]["project_title"], "119章釋經")

    def test_rebuild_failure_does_not_replace_existing_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "final.md"
            path.write_text("## 太 1:1\n\n耶穌基督的家譜。", encoding="utf-8")
            search_index = SermonSearchIndex(root / "search.sqlite3")
            search_index.rebuild_from_manuscripts([_manuscript(path)])

            missing = _manuscript(root / "missing.md", project_id="missing", title="missing")
            response = search_index.rebuild_from_manuscripts([missing])
            status = search_index.status()

        self.assertEqual(response.status, "failed")
        self.assertEqual(status.document_count, 1)

    def test_fallback_agentic_coverage_uses_document_lookup_tool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "final.md"
            path.write_text("## 太 16:19\n\n我要把天國的鑰匙給你。", encoding="utf-8")
            search_index = SermonSearchIndex(root / "search.sqlite3")
            search_index.rebuild_from_manuscripts([
                _manuscript(path, project_id="matt-16", title="16章釋經", bible_verse="太 16:1-19")
            ])
            service = SermonSearchService(index=search_index)
            service.llm.api_key = None

            response = service.query(SermonSearchRequest(question="教授對 16 章釋經都覆蓋了那些 verses?"))

        self.assertIn("document_lookup", response.search_trace.tools_used)
        self.assertIn("document_coverage", response.search_trace.tools_used)
        self.assertIn("Matt.16.1-Matt.16.19", response.answer)

    def test_chapter_coverage_aggregates_matching_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.md"
            second = root / "second.md"
            third = root / "third.md"
            first.write_text("## 太 16:19\n\n我要把天國的鑰匙給你。", encoding="utf-8")
            second.write_text("## 太 16:21-27\n\n耶穌說自己必須受苦，門徒要捨己。", encoding="utf-8")
            third.write_text("## 靈魂體\n\n太 16:25-28 討論生命與賞賜，也旁及太 22:37。", encoding="utf-8")
            search_index = SermonSearchIndex(root / "search.sqlite3")
            search_index.rebuild_from_manuscripts(
                [
                    _manuscript(first, project_id="matt-16-a", title="16章 - 鑰匙", bible_verse="太 16:1-19"),
                    _manuscript(second, project_id="matt-16-b", title="16 章 - 捨己", bible_verse="太 16:21-27"),
                    _manuscript(third, project_id="matt-16-c", title="16 章 - 靈魂體"),
                ]
            )
            service = SermonSearchService(index=search_index)
            service.llm.api_key = None

            response = service.query(SermonSearchRequest(question="教授對 16 章釋經都覆蓋了那些 verses?"))

        self.assertIn("16章 - 鑰匙", response.answer)
        self.assertIn("16 章 - 捨己", response.answer)
        self.assertIn("16 章 - 靈魂體", response.answer)
        self.assertIn("Matt.16.1-Matt.16.19", response.answer)
        self.assertIn("Matt.16.21-Matt.16.27", response.answer)
        self.assertIn("Matt.16.25-Matt.16.28", response.answer)
        self.assertNotIn("明確逐段處理/引用的馬太經文：Matt.22.37", response.answer)

    def test_normal_mode_skips_llm_planner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "final.md"
            path.write_text("## 耶和華的僕人\n\n以賽亞書 42 章討論耶和華僕人的使命。", encoding="utf-8")
            search_index = SermonSearchIndex(root / "search.sqlite3")
            search_index.rebuild_from_manuscripts([_manuscript(path)])
            fake_llm = FakeLLM()
            service = SermonSearchService(index=search_index, llm=fake_llm)

            response = service.query(SermonSearchRequest(question="什麼是耶和華的僕人？"))

        self.assertEqual(response.search_trace.rounds, 1)
        self.assertEqual(fake_llm.planner_calls, 0)
        self.assertEqual(fake_llm.answer_calls, 1)


if __name__ == "__main__":
    unittest.main()
