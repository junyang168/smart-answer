"""Bounded L1 final-source closeout; originals and raw reviews stay immutable."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import re
from pathlib import Path

from backend.pipeline import exegesis_intelligent_grouping_job as core

PROMPT = '''这是第一层最后的原文核验与收口，不是重新定位或重新生成段落。
输入仍包含整卷全部Claim与完整方案，但只处置cases中的最终遗留项。
physical_sources是重新打开并核对SHA的母本连续原文（SVG不进入）。直接读前后文，
区分实质成员/范围分歧、已执行的更正、审核引用抄录错误。不得把candidate转primary，
不得改Claim、primary、其他成员、无争议范围或做第二层grouping。
每项返回confirm_current、modify或unresolved，具体理由及连续逐字短引文。
modify只能移动该finding明确提到或当前单元内的成员，或修改该单元范围；不建新单元。
审核引文错误可用quote_corrections记录原索引的逐字替代，只纠正原source/location；
不得删除引用、用别的来源代替或冒称原审核模型已通过。证据不足返回unresolved并说缺什么。
真正成员/范围更改或推翻审核语义结论后必须再独立整卷复核；只有一次收口，不循环重试。'''


def load_reader(path):
    spec = importlib.util.spec_from_file_location('l1_source_closeout_reader', path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader


def schema(cases):
    evidence = dict(type='object', additionalProperties=False,
                    required=['source_id', 'location', 'quote'],
                    properties={k: dict(type='string', minLength=1) for k in ['source_id', 'location', 'quote']})
    move = dict(type='object', additionalProperties=False, required=['claim_id', 'target_unit_id'],
                properties={k: dict(type='string') for k in ['claim_id', 'target_unit_id']})
    correction = dict(type='object', additionalProperties=False, required=['evidence_index', 'quote'],
                      properties=dict(evidence_index=dict(type='integer', minimum=0), quote=dict(type='string', minLength=1)))
    case = dict(type='object', additionalProperties=False,
                required=['decision', 'reason', 'evidence', 'moves', 'passage_key', 'quote_corrections'],
                properties=dict(decision=dict(type='string', enum=['confirm_current', 'modify', 'unresolved']),
                                reason=dict(type='string', minLength=1), evidence=dict(type='array', items=evidence),
                                moves=dict(type='array', items=move),
                                passage_key=dict(anyOf=[dict(type='string'), dict(type='null')]),
                                quote_corrections=dict(type='array', items=correction)))
    return dict(type='object', additionalProperties=False, required=['dispositions'],
                properties=dict(dispositions=dict(type='object', additionalProperties=False,
                                required=list(cases), properties={k: case for k in cases})))


def prepare(report, proposal, request, reader):
    """Reopen affected sources, never trust model/copied source text as evidence."""
    cases = {k: v for k, v in report['response']['findings'].items() if v['status'] != 'pass'}
    for error in report['evidence_errors']:
        cases.setdefault(error['key'], report['response']['findings'][error['key']])
    units = {u['unit_id']: u for u in proposal['units']}
    claims = {c['id']: c for c in request['claims']}
    relevant = set()
    for key, finding in cases.items():
        relevant.update(claims[a]['source_id'] for a in units[key]['claim_ids'])
        relevant.update(e['source_id'] for e in finding['evidence'])
    physical, index = [], {}
    packet, _ = reader.compile_packet(request) if cases else ({}, {})
    for source in request['sources']:
        sid = source['source_id']
        if sid not in relevant:
            continue
        raw = Path(source['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source['file_sha256']:
            raise ValueError('closeout physical source drift: ' + sid)
        rows = reader.reader.physical_paragraphs(raw.decode())
        anchors = set()
        # Frozen fragment mapping locates evidence; a failed match is never proof.
        view = next(s for s in packet['physical_sources'] if s['source_id'] == sid)
        for link in view['fragment_locations']:
            if any(link['claim_id'] in units[k]['claim_ids'] for k in cases):
                anchors.update(link['physical_locations'])
        for finding in cases.values():
            anchors.update(e['location'] for e in finding['evidence'] if e['source_id'] == sid)
            if sid in finding.get('need_more_context', ''):
                anchors.update(re.findall(r'(?:row|block):\d+', finding['need_more_context']))
        positions = {i for i, (loc, _) in enumerate(rows) if loc in anchors}
        selected = {j for i in positions for j in range(max(0, i-3), min(len(rows), i+4))}
        paragraphs = []
        for i in sorted(selected):
            loc, text = rows[i]
            clean = reader.reader.exclude_svg(text)
            pieces = [clean] if isinstance(clean, str) else [p for p in clean['model_text_parts'] if isinstance(p, str)]
            index[(sid, loc)] = pieces
            paragraphs.append(dict(location=loc, text=clean))
        physical.append(dict(source_id=sid, file_sha256=source['file_sha256'], paragraphs=paragraphs,
                             complete_source=False))
    return cases, physical, index


def verbatim(evidence, index):
    return bool(evidence) and all(e['quote'] and any(e['quote'] in text for text in
        index.get((e['source_id'], e['location']), [])) for e in evidence)


def already_applied(finding, unit, index):
    """Only provable no-op suggestions; a prose claim of 'done' is insufficient."""
    return (finding['status'] == 'change' and not finding['need_more_context']
            and (finding['suggested_passage_key'] in (None, unit['passage_key']))
            and all(m['claim_id'] in unit['claim_ids'] and m['target_unit_id'] == unit['unit_id']
                    and m['new_passage_key'] in (None, unit['passage_key']) for m in finding['moves'])
            and bool(finding['moves'] or finding['suggested_passage_key'])
            and verbatim(finding['evidence'], index))


def apply(response, cases, proposal, report, index, claims, locator, primary_fits):
    """Apply only supported, bounded edits; return derived review and re-review flag."""
    core.exact(response['dispositions'], cases, 'source closeout dispositions')
    effective = copy.deepcopy(proposal)
    derived = copy.deepcopy(report['response'])
    units = {u['unit_id']: u for u in effective['units']}
    by_claim = {c['id']: c for c in claims}
    moved, ranged, attempted = set(), set(), set()
    needs_review = False
    for key, disposition in response['dispositions'].items():
        finding = derived['findings'][key]
        decision = disposition['decision']
        if decision not in {'confirm_current', 'modify', 'unresolved'}:
            raise ValueError('unknown closeout decision')
        if decision == 'modify' and not (disposition['moves'] or disposition['passage_key']):
            raise ValueError('modify requires explicit semantic edits')
        if not disposition['reason'].strip():
            raise ValueError('closeout requires concrete reason')
        if decision == 'unresolved':
            if disposition['moves'] or disposition['passage_key'] or disposition['quote_corrections']:
                raise ValueError('unresolved closeout cannot apply changes')
            continue
        if not verbatim(disposition['evidence'], index):
            raise ValueError('non-verbatim closeout evidence')
        for e in disposition['evidence']:
            allowed_sources = {by_claim[a]['source_id'] for a in units[key]['claim_ids']}
            if e['source_id'] not in allowed_sources:
                raise ValueError('closeout evidence outside affected Claim sources')
        if decision == 'confirm_current' and (disposition['moves'] or disposition['passage_key']):
            raise ValueError('confirm_current cannot modify membership/range')
        if decision == 'modify' and disposition['quote_corrections']:
            raise ValueError('separate semantic changes from citation corrections')
        if disposition['quote_corrections'] and finding['status'] != 'pass':
            raise ValueError('citation correction cannot resolve semantic disagreement')
        corrected = set()
        for correction in disposition['quote_corrections']:
            n = correction['evidence_index']
            if type(n) != int or not 0 <= n < len(finding['evidence']) or n in corrected:
                raise ValueError('invalid/duplicate citation correction index')
            corrected.add(n)
            citation = dict(finding['evidence'][n], quote=correction['quote'])
            if verbatim([finding['evidence'][n]], index) or not verbatim([citation], index):
                raise ValueError('citation correction must repair an actual physical mismatch')
            finding['evidence'][n] = citation
        if finding['status'] != 'pass':
            needs_review = True  # Independent whole-book verification, even when GPT keeps current plan.
        for move in disposition['moves']:
            alias, target = move['claim_id'], move['target_unit_id']
            allowed = set(cases[key]['reviewed_claim_ids']) | {m['claim_id'] for m in cases[key]['moves']}
            if alias not in allowed or alias in attempted or target not in units:
                raise ValueError('unrelated/duplicate/foreign closeout move')
            attempted.add(alias)
            old = effective['units'][effective['assignments'][alias]['unit_index']]
            if old['unit_id'] != target:
                old['claim_ids'].remove(alias)
                units[target]['claim_ids'].append(alias)
                effective['assignments'][alias]['unit_index'] = next(n for n,u in enumerate(effective['units']) if u['unit_id']==target)
                moved.add(alias)
                needs_review = True
        if disposition['passage_key']:
            locator(disposition['passage_key'])
            if key in ranged:
                raise ValueError('duplicate range correction')
            ranged.add(key)
            units[key]['passage_key'] = disposition['passage_key']
            needs_review = True
        # This is a derived disposition, never relabel the saved model response.
        finding.update(status='pass', moves=[], suggested_passage_key=None, need_more_context='')
        if not disposition['quote_corrections']:
            finding['evidence'] = disposition['evidence']
    core.exact([a for u in effective['units'] for a in u['claim_ids']], by_claim, 'closeout coverage')
    for u in effective['units']:
        if not u['claim_ids']:
            raise ValueError('closeout cannot leave empty units')
        for alias in u['claim_ids']:
            primary = by_claim[alias]['primary']
            if primary and (alias in moved or u['unit_id'] in ranged) and not primary_fits(primary, u['passage_key']):
                raise ValueError('closeout violates read-only primary')
    return effective, derived, needs_review
