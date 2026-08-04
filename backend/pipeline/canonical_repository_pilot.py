from __future__ import annotations

from typing import Iterable

from backend.api.canonical_repository.models import (
    BibleReference,
    CanonicalUnit,
    ManuscriptLocator,
    TopicAssignment,
    UnitRelationship,
)
from backend.api.canonical_repository.service import CanonicalRepositoryService, canonical_repository_service
from backend.api.canonical_repository.source_maps import entries_for_line_range, stable_id


THIRD = "16_章_-_榮耀、信心"
FOURTH = "17_章_登山變像_醫治鬼附之子"


def _citation_for_line(service: CanonicalRepositoryService, source_id: str, line: int, evidence_ids: list[str]):
    source_map = service.store.get_source_map(source_id)
    matched = entries_for_line_range(source_map, line, line)
    if not matched:
        raise ValueError(f"Line {line} does not resolve for {source_id}")
    paragraph_keys = [str(item.get("paragraph_key") or item.get("page_file")) for item in matched]
    for citation in service.store.list_citations():
        current_keys = citation.locator.paragraph_keys or ([citation.locator.page_file] if citation.locator.page_file else [])
        if citation.source_id == source_id and current_keys == paragraph_keys:
            for evidence_id in evidence_ids:
                if evidence_id not in citation.evidence_ids:
                    citation.evidence_ids.append(evidence_id)
            service.store.save_citation(citation)
            return citation
    return service.create_citation_from_source_range(source_id, line, line, evidence_ids=evidence_ids)


def _attach(service: CanonicalRepositoryService, unit_id: str, citations: Iterable) -> None:
    unit = service.store.get_unit(unit_id)
    for citation in citations:
        if citation.citation_id not in unit.citation_ids:
            unit.citation_ids.append(citation.citation_id)
    service.store.save_unit(unit)


def migrate_pilot(service: CanonicalRepositoryService = canonical_repository_service) -> dict:
    third_source = service.register_project_source(THIRD)["source"]["source_id"]
    fourth_source = service.register_project_source(FOURTH)["source"]["source_id"]

    amen = [_citation_for_line(service, third_source, 5, ["E001", "E002", "E003", "E004"])]
    _attach(service, "CU-SEED-9b134dc0eb3b", amen)

    dispensational = [
        _citation_for_line(service, third_source, 21, ["E018", "E019", "E020"]),
        _citation_for_line(service, third_source, 23, ["E021", "E022", "E023", "E024"]),
        _citation_for_line(service, fourth_source, 3, ["E003"]),
        _citation_for_line(service, fourth_source, 5, ["E004"]),
    ]
    _attach(service, "CU-SEED-90ab5f9a7652", dispensational)

    transfiguration = [
        _citation_for_line(service, third_source, 51, ["E047", "E048", "E049", "E050", "E051"]),
        _citation_for_line(service, third_source, 55, ["E052", "E053", "E054", "E055", "E056"]),
        _citation_for_line(service, fourth_source, 35, ["E023", "E024", "E025", "E026", "E027", "E028"]),
        _citation_for_line(service, fourth_source, 41, ["E029", "E030", "E031", "E032", "E033", "E034"]),
    ]
    _attach(service, "CU-SEED-d133823f3737", transfiguration)

    small_faith_id = "CU-PILOT-small-faith"
    small_faith_citations = [
        _citation_for_line(service, fourth_source, 49, ["E044", "E045", "E046", "E047", "E048"]),
        _citation_for_line(service, fourth_source, 53, ["E049", "E050", "E051", "E052", "E053", "E054"]),
    ]
    try:
        small_faith = service.store.get_unit(small_faith_id)
    except FileNotFoundError:
        small_faith = CanonicalUnit(
            unit_id=small_faith_id,
            title="「小信」的跨經文神學：從不理解到不倚靠神",
            unit_type="concept",
            primary_bible_refs=[
                BibleReference(osis="Matt.8.26", display="太 8:26"),
                BibleReference(osis="Matt.16.8", display="太 16:8"),
                BibleReference(osis="Matt.17.14-Matt.17.21", display="太 17:14–21"),
            ],
            topic_assignments=[TopicAssignment(topic_ids=["ecclesiology-discipleship", "faith-trust"], path=["教會論與門徒", "門徒的信心、認識與信靠"])],
            manuscript=ManuscriptLocator(
                project_id=FOURTH,
                project_type="transcript",
                heading_title="一、不信、芥菜種信心與倚靠神的能力",
                heading_anchor="一-不信-芥菜種信心與倚靠神的能力",
            ),
            aliases=["小信", "信心小", "芥菜種信心", "倚靠神"],
        )
    for citation in small_faith_citations:
        if citation.citation_id not in small_faith.citation_ids:
            small_faith.citation_ids.append(citation.citation_id)
    service.store.save_unit(small_faith)

    relationships = service.store.list_relationships()
    passage_ids = ["CU-SEED-a0763044663e", "CU-SEED-d0ceaabdb42b"]
    for passage_id in passage_ids:
        relationship = UnitRelationship(
            relationship_id=stable_id("REL", small_faith_id, passage_id, "related_passage"),
            from_unit_id=small_faith_id,
            to_unit_id=passage_id,
            relationship_type="related_passage",
            status="approved",
            reason="The cross-passage topic synthesizes this passage without replacing its exegesis unit.",
        )
        if relationship.relationship_id not in {item.relationship_id for item in relationships}:
            relationships.append(relationship)
        if relationship.relationship_id not in small_faith.relationship_ids:
            small_faith.relationship_ids.append(relationship.relationship_id)
    service.store.save_unit(small_faith)
    service.store.save_relationships(relationships)
    return {"units": 4, "citations": len(amen) + len(dispensational) + len(transfiguration) + len(small_faith_citations)}


if __name__ == "__main__":
    print(migrate_pilot())
