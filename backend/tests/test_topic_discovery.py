"""Topic discovery agent (#459, solution doc §4–6)."""
import json

import pytest

from backend.pipeline import topic_discovery as m


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda seconds: None)


def claim(cid, statement, steps=()):
    return {"claim_id": cid, "statement": statement, "evidence_step_ids": list(steps)}


def step(sid, fragments, refs=()):
    return {"evidence_step_id": sid, "statement": sid, "source_fragment_ids": list(fragments), "scripture_refs": list(refs)}


def fragment(fid, source, paragraph, text):
    return {"fragment_id": fid, "source_id": source, "paragraph_key": paragraph, "verbatim_excerpt": text}


TEXT = {"A": ["first block here.", "seed quote one is here", "next block", "far away block", "other quote two lives here"],
        "B": ["ransom quote three here", "tail"]}


def corpus():
    claims = {
        "C1": claim("C1", "seed", ["E1"]),
        "C2": claim("C2", "related by relation", ["E2"]),
        "C3": claim("C3", "same verse elsewhere", ["E3"]),
        "C4": claim("C4", "far, unconnected, says 多人", []),
        "C5": claim("C5", "same CVP"),
    }
    steps = {"E1": step("E1", ["F1"], ["太 20:28"]), "E2": step("E2", ["F2"]), "E3": step("E3", ["F3"], ["馬太福音20:28"])}
    fragments = {"F1": fragment("F1", "A", "S0002", "seed quote one"), "F2": fragment("F2", "A", "S0009", "other quote two"),
                 "F3": fragment("F3", "B", "S0001", "ransom quote three")}
    relations = [{"from_id": "C1", "to_id": "C2"}]
    links = [{"viewpoint_id": "V1", "claim_id": "C1"}, {"viewpoint_id": "V1", "claim_id": "C5"}]
    sources = {"A": {"source_id": "A", "title": "Sermon A"}, "B": {"source_id": "B", "title": "Sermon B"}}
    return m.Corpus(claims, steps, fragments, relations, links, [], sources)


def sources(c):
    return m.Sources(c, loader=lambda s: {"content": "\n\n".join(TEXT[s["source_id"]])})


def test_walk_reaches_each_edge_and_records_the_path():
    found = m.candidates(corpus(), ["C1"], {"C1"})
    assert set(found) == {"C2", "C3", "C5"}
    assert any(p.startswith("Claim 关系") for p in found["C2"])
    assert any(p.startswith("同一经文 太 20:28") for p in found["C3"])
    assert any(p.startswith("同一观点") for p in found["C5"])


def test_keywords_are_one_more_entry():
    found = m.candidates(corpus(), ["C1"], {"C1"}, ["多人"])
    assert found["C4"] == ["主题词"]


def test_candidate_carries_its_evidence_block_and_neighbours():
    c = corpus()
    assert sources(c).context("C1") == [("A", 1), ("A", 2), ("A", 3)]


def test_shards_stay_under_the_limit_and_share_blocks():
    blocks = {"S001#1": "x" * 100, "S001#2": "y" * 100}
    cards = [{"ref": f"D{n:03d}", "blocks": ["S001#1", "S001#2"]} for n in range(6)]
    shards = m.build_shards(cards, blocks, limit=300)
    assert sum(len(s[0]) for s in shards) == 6 and len(shards) > 1
    for cards_in, blocks_in in shards:
        assert set(blocks_in) == {"S001#1", "S001#2"}


def test_verdicts_must_cover_the_shard_exactly():
    with pytest.raises(ValueError):
        m.check_verdicts([{"item": "D001"}, {"item": "D001"}], ["D001", "D002"])


class Judge:
    """Answers each shard from a table of statement -> verdict; asks to read more once when told to."""

    def __init__(self, table, read_more=()):
        self.table, self.read_more, self.last_raw_response, self.requests = table, set(read_more), None, []

    def generate_json(self, system, text, schema, timeout_seconds=None):
        payload = json.loads(text)
        self.requests.append(payload)
        out = []
        for card in payload["candidates"]:
            verdict = self.table[card["statement"]]
            if card["statement"] in self.read_more and "read_more" in json.dumps(schema):
                out.append({"item": card["ref"], "verdict": "read_more", "reason": "", "read": [
                    {"source": card["blocks"][0].split("#")[0], "start_block": 4, "end_block": 5}]})
                continue
            row = {"item": card["ref"], "verdict": verdict, "reason": "r"}
            if "read_more" in json.dumps(schema):
                row["read"] = []
            out.append(row)
        return {"verdicts": out}


def test_rounds_expand_only_from_accepted_claims(tmp_path):
    c = corpus()
    judge = Judge({"related by relation": "in", "same verse elsewhere": "borderline", "same CVP": "out",
                   "far, unconnected, says 多人": "in"})
    result = m.Discovery(c, sources(c), judge, tmp_path, "t", ["C1"], ["多人"]).run()
    assert result["accepted"] == ["C1", "C2", "C4"]
    assert result["borderline"] == ["C3"]
    assert result["stop"] in ("no_candidates", "no_new")
    assert (tmp_path / "claims.json").exists() and "主题发现" in (tmp_path / "report.md").read_text()
    # Every candidate the model judged came with source text.
    assert all(payload["blocks"] for payload in judge.requests)


def test_read_more_adds_the_requested_blocks_and_rejudges(tmp_path):
    c = corpus()
    judge = Judge({"related by relation": "in", "same verse elsewhere": "out", "same CVP": "out"}, read_more={"related by relation"})
    m.Discovery(c, sources(c), judge, tmp_path, "t", ["C1"]).run()
    reread = [p for p in judge.requests if len(p["candidates"]) == 1 and p["candidates"][0]["statement"] == "related by relation"]
    assert reread and "S001#4" in reread[0]["blocks"]


def test_unknown_seed_stops_before_any_call(tmp_path):
    c = corpus()
    with pytest.raises(ValueError):
        m.Discovery(c, sources(c), Judge({}), tmp_path, "t", ["nope"])


def test_a_source_edited_after_extraction_is_flagged_not_fatal(tmp_path):
    from backend.pipeline.theological_evidence_packet import TheologicalEvidencePacketError

    def loader(s):
        if s["source_id"] == "B":
            raise TheologicalEvidencePacketError("scoped source original SHA mismatch: B")
        return {"content": "\n\n".join(TEXT[s["source_id"]])}

    c = corpus()
    judge = Judge({"related by relation": "out", "same verse elsewhere": "in", "same CVP": "out"})
    result = m.Discovery(c, m.Sources(c, loader=loader), judge, tmp_path, "t", ["C1"]).run()
    card = next(x for p in judge.requests for x in p["candidates"] if x["statement"] == "same verse elsewhere")
    assert card["source_changed"] and card["blocks"] == []
    assert result["sources_changed"] == ["B"] and "Sermon B" in (tmp_path / "report.md").read_text()


def test_every_round_is_judged_against_the_seeds_only(tmp_path):
    c = corpus()
    judge = Judge({"related by relation": "in", "same verse elsewhere": "out", "same CVP": "out"})
    m.Discovery(c, sources(c), judge, tmp_path, "t", ["C1"]).run()
    assert all(p["topic"] == {"name": "t", "seeds": ["seed"], "other_topics": []} for p in judge.requests)


def test_rate_limit_waits_once_then_retries(tmp_path):
    from backend.pipeline.codex_subscription_client import CodexSubscriptionError

    class Limited(Judge):
        def generate_json(self, system, text, schema, timeout_seconds=None):
            if not getattr(self, "refused", False):
                self.refused = True
                raise CodexSubscriptionError("exceeded retry limit, last status: 429 Too Many Requests")
            return super().generate_json(system, text, schema, timeout_seconds)

    c = corpus()
    waits = []
    d = m.Discovery(c, sources(c), Limited({"related by relation": "out", "same verse elsewhere": "out", "same CVP": "out"}),
                    tmp_path, "t", ["C1"])
    d.sleep = waits.append
    d.run()
    assert waits[0] == m.RATE_LIMIT_WAIT_SECONDS


def test_claims_of_another_topic_are_listed_apart(tmp_path):
    class Elsewhere(Judge):
        def generate_json(self, system, text, schema, timeout_seconds=None):
            reply = super().generate_json(system, text, schema, timeout_seconds)
            for v in reply["verdicts"]:
                v["other_topic"] = "揀選" if v["verdict"] == "borderline" else ""
            return reply

    c = corpus()
    judge = Elsewhere({"related by relation": "in", "same verse elsewhere": "borderline", "same CVP": "out"})
    result = m.Discovery(c, sources(c), judge, tmp_path, "t", ["C1"], other_topics=["揀選"]).run()
    assert judge.requests[0]["topic"]["other_topics"] == ["揀選"]
    assert result["other_topics"] == {"揀選": ["C3"]}
    assert "属于另一个主题：揀選" in (tmp_path / "report.md").read_text()
