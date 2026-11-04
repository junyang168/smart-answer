"""Authenticated pending CVP assertions remain history, never current Claim inputs."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from backend.api.canonical_repository.postgres_store import ChangeSetConflict, canonical_json
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.cvp_sql_claim_authority import current_claim_authority
from backend.api.canonical_repository.viewpoint_foundation import ViewpointFoundationValidationError
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts, operation_request
from backend.tests.test_claim_management_human_source_follow_up import authorize
from backend.tests.test_viewpoint_foundation import _foundation_package, _existing_approved_claim


def seeded(action):
    store, _, _, _, root, _ = action
    package, _ = _foundation_package()
    text = package['source_fragments'][0]['verbatim_excerpt']
    file = root / 'foundation-source.json'
    file.write_text(json.dumps({'script': [{'text': text}]}))
    package['source_documents'][0].update(source_path=str(file), source_file_sha256=binding(file)['file_sha256'])
    # The foundation fixture starts with this exact existing reviewed version;
    # generic ingestion correctly cannot create human approval itself.
    row = _existing_approved_claim(package)['claims', 'CL-PETER']
    with store.connect() as conn:
        conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,visibility,content_sha256,payload) VALUES ('claims','CL-PETER',1,'approved','internal',%s,%s::jsonb)",
                     (row['content_sha256'], canonical_json(row['payload'])))
    store.apply_plan(store.plan_package(package))
    return store, root, file, text


@pytest.mark.parametrize('late_edge', [False, True])
def test_claim_retirement_keeps_exact_link_and_pending_obligation_without_current_use(action, late_edge):
    store, root, file, text = seeded(action)
    request, output = operation_request(action, 'Retire', ['CL-PETER'])
    request.source_bindings = {'SRC-1': cm.source.FileBinding.model_validate(binding(file))}
    request.policy = cm.source.FileBinding.model_validate(binding(cm.DEFAULT_POLICY.parent / 'wang_claim_management_opus_only_v1.json'))
    authorize(request)
    old_link = store.get_record_state('viewpoint_claim_links', 'VCL-1')
    old_view = store.get_record_state('canonical_viewpoints', 'CV-1')
    def propose(packet):
        candidate = cm.existing_candidate(packet['before'])
        item = next(d for d in candidate.dispositions if d.record_key == 'claims/CL-PETER')
        item.action = 'retire'; item.reason = 'synthetic reviewed retirement, CVP impact stays pending'
        item.citations = [cm.Citation(source_id='SRC-1', paragraph_key='S0001', exact_quote=text)]
        return candidate.model_dump(mode='json')
    def review(packet):
        if late_edge:
            # A newly arrived consumer was not in the exact reviewed snapshot.
            link = deepcopy(old_link['payload']); link['viewpoint_claim_link_id'] = 'LATE'
            link['link_type'] = 'supports'
            store.apply_plan(store.plan_package({'package_id': 'late-active-link', 'viewpoint_claim_links': [link]}))
        from backend.tests.test_claim_management_execution_postgres import passing
        return passing(packet)
    ports = SyntheticPorts(store, root, proposal=propose, opus=review)
    result = execute_claim_management(store, request, root=output, ports=ports)
    assert ports.calls == ['gpt', 'opus']
    parent = store.get_cvp_group('CMA-' + cm.sha256_json(request.action_id))
    child = store.get_cvp_group(next(iter(parent['latest_rounds'].values())))
    if late_edge:
        assert result['status'] == 'paused', result
        assert not store.get_record_state('claims', 'CL-PETER')['retired']
        assert not child['change_set_ids']
        return
    assert result['status'] == 'completed', result
    assert store.get_record_state('viewpoint_claim_links', 'VCL-1') == old_link
    assert store.get_record_state('canonical_viewpoints', 'CV-1') == old_view
    pending = child['reviewed']['evidence']['pending_cvp_management_record_keys']
    assert 'viewpoint_claim_links/VCL-1' in pending
    from backend.pipeline.cvp_sql_actions import validate_claim_management_package
    for bad_pending in [[], [*pending, 'viewpoint_claim_links/FOREIGN']]:
        forged = deepcopy(child['reviewed']['evidence']['package']['payload'])
        forged['source_repair_application']['pending_consumers'] = bad_pending
        with pytest.raises(ValueError, match='pending historical consumer'):
            validate_claim_management_package(forged)
    state = store.get_record_state('claims', 'CL-PETER')
    assert state['retired']
    assert not (store.claim_management_qualification('CL-PETER') or {}).get('ready')
    with pytest.raises(ValueError, match='held or not reviewed'):
        current_claim_authority(store, 'CL-PETER', state)
    # The historical exception cannot create a fresh active assertion.
    new_link = deepcopy(old_link['payload']); new_link['viewpoint_claim_link_id'] = 'NEW'
    with pytest.raises((ChangeSetConflict, ViewpointFoundationValidationError), match='non-current endpoints|missing claim'):
        store.apply_plan(store.plan_package({'package_id': 'new-retired-endpoint', 'viewpoint_claim_links': [new_link]}))


def test_unapproved_generic_retirement_still_refuses_live_cvp_link(action):
    store, _, _, _ = seeded(action)
    plan = store.plan_retirement([('claims', 'CL-PETER')], reason='unreviewed generic retirement', package_id='strict-retirement')
    with pytest.raises(ChangeSetConflict):
        store.apply_plan(plan)
    assert not store.get_record_state('claims', 'CL-PETER')['retired']
