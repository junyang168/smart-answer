import assert from "node:assert/strict";
import test from "node:test";
import { collectArticleSources } from "../src/app/resources/wang-repository/articles/[slug]/article-sources.ts";

const src = (source_type, title, full_source_url) => ({ source_type, title, full_source_url });

test("lists each source once and counts the paragraphs that cite it", () => {
  const sources = collectArticleSources([
    { annotation_id: "p1", sources: [src("sermon_transcript", "A", "/resources/sermons/A"), src("sermon_transcript", "A", "/resources/sermons/A")] },
    { annotation_id: "p2", sources: [src("sermon_transcript", "A", "/resources/sermons/A"), src("notes_manuscript", "8章", null)] },
  ]);
  assert.deepEqual(sources, [
    { kind: "sermon", title: "A", url: "/resources/sermons/A", paragraphs: 2 },
    { kind: "notes", title: "8章", url: null, paragraphs: 1 },
  ]);
});

test("sermons come before notes, most-cited first, and a later link fills a missing one", () => {
  const sources = collectArticleSources([
    { annotation_id: "p1", sources: [src("notes_manuscript", "N", null), src("sermon_transcript", "B", "/b")] },
    { annotation_id: "p2", sources: [src("notes_manuscript", "N", "/n"), src("sermon_transcript", "C", "/c"), src("sermon_transcript", "B", "/b")] },
  ]);
  assert.deepEqual(sources.map((s) => [s.title, s.url, s.paragraphs]), [["B", "/b", 2], ["C", "/c", 1], ["N", "/n", 2]]);
});

test("an article without source annotations has no sources", () => {
  assert.deepEqual(collectArticleSources([]), []);
});
