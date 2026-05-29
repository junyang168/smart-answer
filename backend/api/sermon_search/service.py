from __future__ import annotations

import re
from collections import Counter
from typing import Dict, Iterable, Iterator, List, Sequence

from .deepseek_client import DeepSeekClient
from .index_store import SermonSearchIndex
from .models import (
    Citation,
    IndexStatus,
    ReindexRequest,
    ReindexResponse,
    SearchRoundTrace,
    SearchTrace,
    SermonSearchRequest,
    SermonSearchResponse,
    SourceCard,
    SourceUnit,
)


class SermonSearchService:
    def __init__(self, index: SermonSearchIndex | None = None, llm: DeepSeekClient | None = None) -> None:
        self.index = index or SermonSearchIndex()
        self.llm = llm or DeepSeekClient()

    def status(self) -> IndexStatus:
        return self.index.status()

    def reindex(self, request: ReindexRequest) -> ReindexResponse:
        return self.index.rebuild(
            series_ids=request.series_ids or None,
            project_types=request.project_types or ["sermon_note"],
            include_embeddings=request.include_embeddings,
        )

    def query(self, request: SermonSearchRequest) -> SermonSearchResponse:
        sources, units, trace, observations = self._retrieve(request)
        answer, citations, related = self._answer(request, units, sources, observations)
        return SermonSearchResponse(
            answer=answer,
            citations=citations,
            sources=sources,
            related_questions=related,
            search_trace=trace,
        )

    def stream_query_events(self, request: SermonSearchRequest) -> Iterator[dict]:
        sources, units, trace, observations = self._retrieve(request)
        yield {
            "type": "sources",
            "sources": [source.model_dump() for source in sources],
            "search_trace": trace.model_dump(),
        }

        coverage_answer = self._coverage_answer(observations)
        if coverage_answer and (not self.llm.available or self._is_coverage_question(request.question)):
            yield {"type": "answer_delta", "delta": coverage_answer}
            yield {
                "type": "done",
                "citations": [citation.model_dump() for citation in self._fallback_citations(units[:5])],
                "related_questions": [],
            }
            return

        if not units:
            yield {"type": "answer_delta", "delta": "目前索引中沒有找到足夠的講稿依據來回答這個問題。"}
            yield {"type": "done", "citations": [], "related_questions": []}
            return

        if not self.llm.available:
            answer, citations, related = self._extractive_answer(request.question, units, sources)
            yield {"type": "answer_delta", "delta": answer}
            yield {
                "type": "done",
                "citations": [citation.model_dump() for citation in citations],
                "related_questions": related,
            }
            return

        streamed_any = False
        try:
            for delta in self.llm.stream_text(
                self._stream_answer_messages(request.question, units, observations, request.mode.value),
                request.mode.value,
            ):
                streamed_any = True
                yield {"type": "answer_delta", "delta": delta}
            yield {
                "type": "done",
                "citations": [citation.model_dump() for citation in self._fallback_citations(units[:6])],
                "related_questions": [],
            }
        except Exception as exc:
            if streamed_any:
                yield {"type": "answer_delta", "delta": f"\n\n（串流回答中斷：{exc}）"}
                yield {
                    "type": "done",
                    "citations": [citation.model_dump() for citation in self._fallback_citations(units[:6])],
                    "related_questions": [],
                }
                return
            answer, citations, related = self._extractive_answer(request.question, units, sources)
            yield {"type": "answer_delta", "delta": f"{answer}\n\n（DeepSeek 串流失敗，已改用檢索摘要：{exc}）"}
            yield {
                "type": "done",
                "citations": [citation.model_dump() for citation in citations],
                "related_questions": related,
            }

    def _retrieve(
        self,
        request: SermonSearchRequest,
    ) -> tuple[List[SourceCard], List[SourceUnit], SearchTrace, Sequence[dict]]:
        self._ensure_index()
        max_rounds = 4 if request.mode.value == "deep" else 1
        target_k = request.top_k or (30 if request.mode.value == "deep" else 12)
        all_cards: Dict[str, SourceCard] = {}
        round_traces: List[SearchRoundTrace] = []
        tools: set[str] = set()
        notes: List[str] = []
        search_cache: Dict[str, tuple[List[SourceCard], List[str]]] = {}
        state: dict = {
            "question": request.question,
            "round": 0,
            "evidence_count": 0,
            "tools_used": [],
            "notes": [],
            "observations": [],
        }

        for round_index in range(1, max_rounds + 1):
            state["round"] = round_index
            state["evidence_count"] = len(all_cards)
            state["tools_used"] = sorted(tools)
            plan = self._plan_next_searches(request, state)
            selected: List[SourceCard] = []
            round_candidate_count = 0
            round_tools: List[str] = []

            for search in self._normalize_searches(plan):
                tool = str(search.get("tool") or "multi_index_search")
                round_tools.append(tool)
                tools.add(tool)
                if tool == "document_lookup":
                    document_query = str(search.get("document_query") or search.get("query") or request.question)
                    documents = self.index.find_documents(document_query, limit=int(search.get("limit") or 8))
                    state["observations"].append(
                        {
                            "tool": "document_lookup",
                            "query": document_query,
                            "documents": documents,
                        }
                    )
                    round_candidate_count += len(documents)
                    continue
                if tool == "document_coverage":
                    document_query = str(search.get("document_query") or request.question)
                    observation, cards = self._document_coverage_observation(document_query)
                    state["observations"].append(observation)
                    round_candidate_count += observation.get("unit_count", 0)
                    for card in cards:
                        existing = all_cards.get(card.source_id)
                        if not existing or card.score > existing.score:
                            all_cards[card.source_id] = card
                    selected = self._rank_cards(all_cards.values())[:target_k]
                    continue

                query_text = str(search.get("query") or request.question)
                cache_key = self._search_cache_key(query_text, request)
                if cache_key in search_cache:
                    cards, used = search_cache[cache_key]
                else:
                    cards, used = self.index.search(query_text, request.filters, limit=80)
                    search_cache[cache_key] = (cards, used)
                round_candidate_count += len(cards)
                tools.update(used)
                round_tools.extend(used)
                for card in cards:
                    existing = all_cards.get(card.source_id)
                    if not existing or card.score > existing.score:
                        all_cards[card.source_id] = card
                selected = self._rank_cards(all_cards.values())[:target_k]

            if state.get("notes"):
                notes.extend(note for note in state["notes"] if note not in notes)
            round_traces.append(
                SearchRoundTrace(
                    round=round_index,
                    tools_used=sorted(set(round_tools)),
                    query=" | ".join(
                        str(s.get("query") or s.get("document_query") or request.question)
                        for s in self._normalize_searches(plan)
                    ),
                    candidate_count=round_candidate_count,
                    selected_count=len(selected),
                )
            )
            assessment = self._assess_evidence(request, selected, plan)
            notes.extend(assessment.get("notes", []))
            state["selected_sources"] = [
                {
                    "source_id": card.source_id,
                    "doc_title": card.doc_title,
                    "topics": card.topics[:5],
                    "canonical_refs": card.canonical_refs[:8],
                }
                for card in selected[:8]
            ]
            if assessment.get("sufficient"):
                break

        sources = self._rank_cards(all_cards.values())[:target_k]
        answer_limit = 18 if request.mode.value == "deep" else 8
        units = self.index.load_units([source.source_id for source in sources[:answer_limit]])
        trace = SearchTrace(
            mode=request.mode,
            rounds=len(round_traces),
            tools_used=sorted(tools),
            notes=notes + ([] if self.llm.available else ["DeepSeek API key missing; used fallback planner/extractive answer."]),
            round_traces=round_traces,
        )
        return sources, units, trace, state["observations"]

    def _plan_next_searches(self, request: SermonSearchRequest, state: dict) -> dict:
        if request.mode.value == "normal":
            return self._fallback_plan(request, state)
        if self.llm.available:
            try:
                return self.llm.generate_json(self._planner_messages(request, state), request.mode.value)
            except Exception as exc:
                state.setdefault("notes", []).append(f"Planner failed; used fallback planner: {exc}")
        return self._fallback_plan(request, state)

    def _search_cache_key(self, query_text: str, request: SermonSearchRequest) -> str:
        filters = "|".join(
            [
                ",".join(request.filters.series_ids),
                ",".join(request.filters.project_types),
                ",".join(request.filters.topics),
                ",".join(request.filters.canonical_refs),
                ",".join(request.filters.content_types),
            ]
        )
        return f"{query_text}\n{filters}"

    def _planner_messages(self, request: SermonSearchRequest, state: dict) -> List[Dict[str, str]]:
        system = (
            "你是 sermon corpus 的 agentic search planner。"
            "你只能輸出 JSON，不要回答問題。"
            "你要根據問題、目前 observations、可用工具規劃下一輪 search。"
            "可用工具："
            "1) document_lookup: 在全部文件中找候選 manuscript。需要 document_query。"
            "2) document_coverage: 統計某份 manuscript/doc 覆蓋哪些經節。需要 document_query。"
            "3) multi_index_search: 一般檢索，會同時查 canonical refs、topic、full text。需要 query。"
            "若問題是在問某份文件/講稿/章釋經覆蓋哪些 verses/經節，先使用 document_lookup，再使用 document_coverage。"
            "若問題是在問某節經文或神學主題如何解釋，使用 multi_index_search。"
            "若 observations 顯示證據不足，換一個 query 或先 lookup 文件，不要重複同一個工具輸入。"
        )
        user = f"""
問題：{request.question}

目前狀態：
{state}

輸出 JSON：
{{
  "intent": "document_coverage | passage_qa | topic_qa | mixed | unknown",
  "reason": "簡短說明",
  "searches": [
    {{"tool": "document_lookup", "document_query": "16章釋經"}},
    {{"tool": "document_coverage", "document_query": "16章釋經"}},
    {{"tool": "multi_index_search", "query": "太 16:19 天國鑰匙 綑綁 釋放"}}
  ]
}}
"""
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _normalize_searches(self, plan: dict) -> List[dict]:
        searches = plan.get("searches")
        if not isinstance(searches, list) or not searches:
            return [{"tool": "multi_index_search", "query": ""}]
        normalized: List[dict] = []
        for raw in searches[:5]:
            if not isinstance(raw, dict):
                continue
            tool = str(raw.get("tool") or "multi_index_search")
            if tool not in {"document_lookup", "document_coverage", "multi_index_search"}:
                tool = "multi_index_search"
            item = dict(raw)
            item["tool"] = tool
            normalized.append(item)
        return normalized or [{"tool": "multi_index_search", "query": ""}]

    def _fallback_plan(self, request: SermonSearchRequest, state: dict) -> dict:
        question = request.question.strip()
        match = re.search(r"(?:教授對|教授对)?\s*(.+?)\s*(?:都)?(?:覆蓋|覆盖).*(?:verse|verses|經節|经节)", question, re.IGNORECASE)
        if match:
            document_query = match.group(1).strip(" ?？")
            return {
                "intent": "document_coverage",
                "reason": "fallback planner detected a document coverage question",
                "searches": [
                    {"tool": "document_lookup", "document_query": document_query},
                    {"tool": "document_coverage", "document_query": document_query},
                ],
            }
        if state.get("round", 1) > 1:
            expanded = self._expand_query_from_state(question, state)
        else:
            expanded = question
        return {
            "intent": "mixed",
            "reason": "fallback planner runs multi-index search",
            "searches": [{"tool": "multi_index_search", "query": expanded}],
        }

    def _assess_evidence(self, request: SermonSearchRequest, selected: Sequence[SourceCard], plan: dict) -> dict:
        searches = self._normalize_searches(plan)
        ran_coverage = any(search.get("tool") == "document_coverage" for search in searches)
        if ran_coverage and selected:
            return {"sufficient": True, "notes": []}
        if self._evidence_sufficient(selected, request.mode.value):
            return {"sufficient": True, "notes": []}
        return {"sufficient": False, "notes": [f"Insufficient evidence after intent {plan.get('intent', 'unknown')}; continuing."]}

    def _document_coverage_observation(self, doc_query: str) -> tuple[dict, List[SourceCard]]:
        units = self.index.find_document_units(doc_query)
        if not units:
            return (
                {
                    "tool": "document_coverage",
                    "query": doc_query,
                    "found": False,
                    "unit_count": 0,
                    "answer": f"找不到符合 `{doc_query}` 的講稿。",
                },
                [],
            )

        matt_counter: Counter[str] = Counter()
        cross_counter: Counter[str] = Counter()
        doc_scope_matt: List[str] = []
        explicit_matt: List[str] = []
        for ref in units[0].document_scope_refs:
            if ref.book == "Matt" and ref.osis not in doc_scope_matt:
                doc_scope_matt.append(ref.osis)
        for unit in units:
            for ref in unit.all_canonical_refs:
                if ref.book == "Matt":
                    matt_counter[ref.osis] += 1
                    if ref.osis not in explicit_matt:
                        explicit_matt.append(ref.osis)
                else:
                    cross_counter[ref.osis] += 1
        doc_title = units[0].project_title
        broad = doc_scope_matt or [ref for ref, count in matt_counter.items() if count == len(units)]
        target_prefix = None
        if broad:
            first = broad[0].split("-")[0].split(".")
            if len(first) >= 2:
                target_prefix = ".".join(first[:2]) + "."
        specific = [
            ref
            for ref in explicit_matt
            if ref not in broad and (not target_prefix or ref.startswith(target_prefix))
        ]
        other_matt = [
            ref
            for ref in explicit_matt
            if ref not in broad and ref not in specific
        ]
        cross_refs = [*other_matt, *[ref for ref, _ in cross_counter.most_common(12)]]
        lines = [f"`{doc_title}` 這份講稿的索引覆蓋如下：", ""]
        if broad:
            lines.append(f"文件層級範圍：{', '.join(broad)}")
        if specific:
            lines.append(f"明確逐段處理/引用的馬太經文：{', '.join(specific)}")
        if cross_refs:
            lines.append(f"主要交叉經文：{', '.join(cross_refs)}")
        lines.append("")
        lines.append(f"索引來源單元數：{len(units)}")
        sources = [
            SourceCard(
                source_id=unit.source_id,
                content_id=unit.project_id,
                score=100.0 - index,
                doc_title=unit.project_title,
                series_title=unit.series_title,
                lecture_title=unit.lecture_title,
                heading_path=unit.heading_path,
                snippet=self._short_quote(unit.text, 180),
                topics=unit.topic_tags,
                canonical_refs=[ref.osis for ref in unit.all_canonical_refs],
            )
            for index, unit in enumerate(units[:12])
        ]
        return (
            {
                "tool": "document_coverage",
                "query": doc_query,
                "found": True,
                "document_title": doc_title,
                "document_scope_refs": broad,
                "explicit_matthew_refs": specific,
                "cross_refs": cross_refs,
                "unit_count": len(units),
                "answer": "\n".join(lines),
            },
            sources,
        )

    def _ensure_index(self) -> None:
        status = self.index.status()
        if status.source_unit_count == 0:
            self.index.rebuild(project_types=["sermon_note"])

    def _rank_cards(self, cards: Iterable[SourceCard]) -> List[SourceCard]:
        return sorted(cards, key=lambda card: card.score, reverse=True)

    def _evidence_sufficient(self, cards: Sequence[SourceCard], mode: str) -> bool:
        if mode == "deep":
            return len(cards) >= 18
        return len(cards) >= 8

    def _expand_query_from_state(self, question: str, state: dict) -> str:
        selected = state.get("selected_sources") or []
        topics: List[str] = []
        refs: List[str] = []
        for item in selected[:8]:
            if not isinstance(item, dict):
                continue
            topics.extend(str(topic) for topic in item.get("topics", []) if topic)
            refs.extend(str(ref) for ref in item.get("canonical_refs", []) if ref)
        extras = " ".join([*topics[:12], *refs[:12]])
        return f"{question}\n{extras}".strip()

    def _answer(
        self,
        request: SermonSearchRequest,
        units: Sequence[SourceUnit],
        sources: Sequence[SourceCard],
        observations: Sequence[dict],
    ) -> tuple[str, List[Citation], List[str]]:
        coverage_answer = self._coverage_answer(observations)
        if coverage_answer and (not self.llm.available or self._is_coverage_question(request.question)):
            return coverage_answer, self._fallback_citations(units[:5]), []
        if not units:
            return (
                "目前索引中沒有找到足夠的講稿依據來回答這個問題。",
                [],
                [],
            )
        if not self.llm.available:
            return self._extractive_answer(request.question, units, sources)
        try:
            payload = self.llm.generate_json(
                self._answer_messages(request.question, units, observations, request.mode.value),
                request.mode.value,
            )
            return self._verified_llm_payload(payload, units)
        except Exception as exc:
            if coverage_answer:
                return (f"{coverage_answer}\n\n（DeepSeek 回答失敗，已使用工具彙整結果：{exc}）", self._fallback_citations(units[:5]), [])
            answer, citations, related = self._extractive_answer(request.question, units, sources)
            return (f"{answer}\n\n（DeepSeek 回答失敗，已改用檢索摘要：{exc}）", citations, related)

    def _answer_messages(
        self,
        question: str,
        units: Sequence[SourceUnit],
        observations: Sequence[dict],
        mode: str,
    ) -> List[Dict[str, str]]:
        text_limit = 2200 if mode == "deep" else 1400
        evidence = "\n\n".join(
            [
                (
                    f"<source id=\"{unit.source_id}\" doc=\"{unit.project_title}\" "
                    f"heading=\"{' > '.join(unit.heading_path)}\">\n"
                    f"{self._evidence_text(unit.text, text_limit)}\n</source>"
                )
                for unit in units
            ]
        )
        observation_text = "\n".join(self._format_observation(observation) for observation in observations[-8:])
        system = (
            "你是 Dr. Wang 馬太福音釋經講稿的檢索問答助手。"
            "這個語料同時按正典經文與神學主題組織。"
            "只能根據提供的 source 回答；不要使用外部知識補充成為主要論據。"
            "每個主要論點都要引用 source_id。若證據不足，請明說。"
            "請只輸出 JSON。"
        )
        user = f"""
問題：
{question}

講稿證據：
{evidence}

工具 observations：
{observation_text}

輸出 JSON 格式：
{{
  "answer": "完整中文回答",
  "citations": [
    {{"source_id": "...", "quote": "短引文", "supports": "此引文支持的論點"}}
  ],
  "related_questions": ["..."]
}}
"""
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _stream_answer_messages(
        self,
        question: str,
        units: Sequence[SourceUnit],
        observations: Sequence[dict],
        mode: str,
    ) -> List[Dict[str, str]]:
        text_limit = 2200 if mode == "deep" else 1400
        evidence = "\n\n".join(
            [
                (
                    f"<source id=\"{unit.source_id}\" doc=\"{unit.project_title}\" "
                    f"heading=\"{' > '.join(unit.heading_path)}\">\n"
                    f"{self._evidence_text(unit.text, text_limit)}\n</source>"
                )
                for unit in units
            ]
        )
        observation_text = "\n".join(self._format_observation(observation) for observation in observations[-8:])
        system = (
            "你是 Dr. Wang 馬太福音釋經講稿的檢索問答助手。"
            "只能根據提供的 source 回答；不要使用外部知識補充成為主要論據。"
            "用繁體中文直接回答，不要輸出 JSON。"
            "每個主要論點後面都要標出 source_id，例如（source 35480499140f-0028）。"
        )
        user = f"""
問題：
{question}

講稿證據：
{evidence}

工具 observations：
{observation_text}
"""
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _evidence_text(self, text: str, limit: int) -> str:
        cleaned = text.strip()
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 1].rstrip() + "…"

    def _coverage_answer(self, observations: Sequence[dict]) -> str | None:
        for observation in reversed(observations):
            if observation.get("tool") == "document_coverage" and observation.get("found"):
                return str(observation.get("answer") or "").strip() or None
        return None

    def _is_coverage_question(self, question: str) -> bool:
        return bool(re.search(r"(?:覆蓋|覆盖).*(?:verse|verses|經節|经节)", question, re.IGNORECASE))

    def _format_observation(self, observation: dict) -> str:
        if observation.get("tool") == "document_lookup":
            docs = observation.get("documents") or []
            doc_lines = [
                f"{doc.get('project_id')} / {doc.get('project_title')} / score={doc.get('score')}"
                for doc in docs[:5]
            ]
            return f"document_lookup query={observation.get('query')}: " + "; ".join(doc_lines)
        if observation.get("tool") == "document_coverage":
            return (
                f"document_coverage query={observation.get('query')} found={observation.get('found')} "
                f"document={observation.get('document_title')} refs={observation.get('explicit_matthew_refs')} "
                f"cross_refs={observation.get('cross_refs')}"
            )
        return str(observation)

    def _verified_llm_payload(self, payload: dict, units: Sequence[SourceUnit]) -> tuple[str, List[Citation], List[str]]:
        unit_map = {unit.source_id: unit for unit in units}
        citations: List[Citation] = []
        for item in payload.get("citations", []):
            source_id = item.get("source_id")
            unit = unit_map.get(source_id)
            if not unit:
                continue
            quote = str(item.get("quote") or "").strip()
            if quote and quote not in unit.text:
                quote = self._short_quote(unit.text)
            citations.append(
                Citation(
                    source_id=unit.source_id,
                    doc_title=unit.project_title,
                    heading_path=unit.heading_path,
                    quote=quote or self._short_quote(unit.text),
                    supports=str(item.get("supports") or ""),
                )
            )
        if not citations:
            citations = self._fallback_citations(units[:4])
        return (
            str(payload.get("answer") or "").strip() or "講稿證據不足，無法形成可靠回答。",
            citations,
            [str(q) for q in payload.get("related_questions", []) if q][:5],
        )

    def _extractive_answer(
        self,
        question: str,
        units: Sequence[SourceUnit],
        sources: Sequence[SourceCard],
    ) -> tuple[str, List[Citation], List[str]]:
        lines = ["根據目前檢索到的講稿，最相關的材料集中在以下幾處："]
        for source in sources[:5]:
            heading = " > ".join(source.heading_path) if source.heading_path else source.doc_title
            lines.append(f"- {source.doc_title} / {heading}：{source.snippet}")
        return "\n".join(lines), self._fallback_citations(units[:5]), []

    def _fallback_citations(self, units: Sequence[SourceUnit]) -> List[Citation]:
        return [
            Citation(
                source_id=unit.source_id,
                doc_title=unit.project_title,
                heading_path=unit.heading_path,
                quote=self._short_quote(unit.text),
                supports="檢索到的相關講稿段落",
            )
            for unit in units
        ]

    def _short_quote(self, text: str, limit: int = 120) -> str:
        cleaned = " ".join(text.split())
        if len(cleaned) <= limit:
            return cleaned
        return cleaned[: limit - 1].rstrip() + "…"


sermon_search_service = SermonSearchService()
