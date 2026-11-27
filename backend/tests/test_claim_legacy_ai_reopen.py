"""Legacy AI suggestions re-enter normal review only under exact owner scope."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_management_intake as intake, claim_management_batch as batch
from backend.pipeline.claim_legacy_ai_reopen import marker
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts

NOTE = '独立 AI 复审：changes_suggested 核实关系方向。 [artifact sermon.independent-review.json sha256:49cea7bf592c]'


def seed(store, fault=None):
    if fault == 'earlier_human':
        store.record_review('claims', 'C', decision='candidate', reviewer_kind='human')
    store.record_review('claims', 'C', decision='human_review_required',
        reviewer_kind='human' if fault == 'human' else 'ai', reviewer_id='legacy-model',
        reason='source unresolved' if fault == 'source_uncertain' else NOTE)
    state = store.get_record_state('claims', 'C')
    if fault == 'changed_revision':
        store.record_review('claims', 'C', decision='human_review_required', reviewer_kind='system', reason='later hold')
    if fault in {'inherited_occurrences','changed_teaching','changed_note'}:
        payload=deepcopy(state['payload'])
        if fault=='inherited_occurrences':payload['occurrences'][0]['canonical_evidence_step_ids']=['EC']
        elif fault=='changed_teaching':payload['statement']='a different later teaching'
        else:payload['review_note']='different unresolved source decision'
        # Disposable SQL fixture: an old edit inherited the AI flag but did
        # not generate a new review. Do not forge a sealed content approval.
        from backend.api.canonical_repository.postgres_store import record_content_sha
        payload['revision']=state['revision']+1
        digest=record_content_sha(payload)
        with store.connect() as conn:
            conn.execute("""INSERT INTO wang_knowledge.object_versions
                (collection,object_id,revision,content_sha256,payload,change_set_id)
                SELECT 'claims','C',%s,%s,%s::jsonb,change_set_id FROM wang_knowledge.object_versions
                WHERE collection='claims' AND object_id='C' AND revision=%s""",
                (payload['revision'],digest,json.dumps(payload),state['revision']))
            conn.execute("UPDATE wang_knowledge.objects SET revision=%s,content_sha256=%s,payload=%s::jsonb WHERE collection='claims' AND object_id='C'",
                (payload['revision'],digest,json.dumps(payload)))
    return state


@pytest.mark.parametrize('fault', [None, 'inherited_occurrences', 'changed_teaching', 'changed_note', 'human', 'earlier_human', 'source_uncertain', 'changed_revision'])
def test_reopening_is_opt_in_and_does_not_promote_other_holds(claims, fault):
    store, root = claims
    seed(store, fault)
    before = store.get_record_state('claims', 'C')
    default = intake.prepare(['C'], root/'default', instruction='Update C', store=store)
    assert not default['manifest'] and default['excluded']['C']['reason'] == 'human_hold'
    prepared = intake.prepare(['C'], root/'explicit', instruction='Update C', store=store,
        legacy_ai_findings_direction='所有 human review required 都要 refresh')
    if fault not in {None,'inherited_occurrences'}:
        assert not prepared['manifest'] and prepared['excluded']['C']['reason'] == 'human_hold'
        assert store.get_record_state('claims', 'C') == before
        return
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    def review(packet):
        assert NOTE in json.dumps(packet, ensure_ascii=False)
        row = next(x for x in packet['candidate']['records'] if x['object_id'] == 'C')
        assert row['payload']['statement'] == before['payload']['statement']
        assert row['payload']['review_status'] == 'candidate'
        return pass_review(packet)
    with store.connect() as conn:
        events = conn.execute("SELECT * FROM wang_knowledge.review_events WHERE object_id='C'").fetchall()
    ports = SyntheticPorts(store, execution, opus=review)
    result = execute_claim_management(store, request, root=execution, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']
    after = store.get_record_state('claims', 'C')
    assert after['payload']['review_status'] == 'candidate'
    assert after['payload']['statement'] == before['payload']['statement']
    assert store.claim_management_qualification('C')['ready']
    with store.connect() as conn:
        assert conn.execute("SELECT * FROM wang_knowledge.review_events WHERE object_id='C'").fetchall() == events


def test_new_human_decision_blocks_prepared_reopening(claims):
    store, root = claims
    seed(store)
    prepared = intake.prepare(['C'], root, instruction='Update C', store=store,
        legacy_ai_findings_direction='Refresh old AI flags')
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, execution = next(iter(jobs.values()))
    before = store.get_record_state('claims', 'C')
    def review(packet):
        with store.connect() as conn:
            conn.execute("""INSERT INTO wang_knowledge.review_events
                (review_event_id,collection,object_id,object_revision,reviewer_id,reviewer_kind,decision,reason,artifact)
                VALUES ('HUMAN-LATE','claims','C',%s,'owner','human','human_review_required','keep hold','{}'::jsonb)""",
                (before['revision'],))
        return pass_review(packet)
    result = execute_claim_management(store, request, root=execution,
        ports=SyntheticPorts(store, execution, opus=review))
    assert result['status'] == 'paused', result
    assert store.get_record_state('claims', 'C') == before


def test_model_cannot_hide_teaching_edit_in_program_reset(claims):
    from backend.pipeline.claim_metadata_projection import compile_projection, project_candidate
    store, root = claims
    seed(store)
    prepared = intake.prepare(['C'], root, instruction='Update C', store=store,
        legacy_ai_findings_direction='Refresh old AI flags')
    _, jobs = batch.load(prepared['manifest']['path'], root/'batch', store=store)
    request, _ = next(iter(jobs.values()))
    state = cm.model_state(store.get_record_state('claims', 'C'))
    before = {'claims/C': state}
    policy, sources = cm.load_authority(request)
    with store.connect() as conn, conn.cursor() as cur:
        projection = compile_projection(cur, request, before, policy)
    candidate = cm.existing_candidate(before)
    candidate.records.append(cm.source.Row(collection='claims', object_id='C', payload=deepcopy(state['payload'])))
    candidate.records[0].payload['statement'] = 'unapproved different teaching'
    candidate.dispositions[0].action = 'update'
    candidate, _ = project_candidate(candidate, {}, {'unit_request': request.model_dump(mode='json'),
        'before': before, 'program_metadata_projection': projection})
    with pytest.raises(ValueError, match='own-source evidence'):
        cm.validate_candidate(request, before, candidate, sources, metadata_projection=projection)
