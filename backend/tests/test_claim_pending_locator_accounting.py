"""Exact deferred CVP structure is not Claim approval or group completion."""
from copy import deepcopy
from pathlib import Path
import os
import pytest


@pytest.mark.skipif(os.getenv('WKP357_PENDING_LOCATOR_SAVED') != '1', reason='explicit current saved SQL proof')
@pytest.mark.parametrize('fault', [None, 'finding', 'duplicate', 'omit', 'foreign', 'pin',
                                  'statement', 'source', 'semantic', 'link', 'history', 'write_set', 'approval'])
def test_actual_pending_locator_accounting(fault):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import claim_source_write_slice as m, source_reconciliation as c
    from backend.pipeline.claim_first_management import validate_claim_audit_report
    from backend.pipeline.cvp_sql_flow import validate_transaction_audit
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    load_dotenv('.env'); store = PostgresKnowledgeStore()
    run = 'CVPG-521c60c1fdc0b9fb1fe5e9704781ee845fbe9edc587efbcfd6e2d8acd4f65bad'
    original = store.get_cvp_group(run); saved = deepcopy(original)
    root = Path(saved['reviewed']['evidence']['package']['payload']['source_repair_application']['run_root'])
    files = {p:c.file_sha256(p) for p in root.rglob('*') if p.is_file()}
    audit = read_artifact(root/'claim-first-audit/audit.json'); old_audit = deepcopy(audit)
    findings = audit['layers']['2']['component_locator_findings']
    if fault == 'finding': findings[0]['spans'][0]['at_offsets'] += 'x'
    if fault == 'duplicate': findings.append(deepcopy(findings[0]))
    if fault == 'omit': findings.clear()
    if fault == 'foreign': findings[0]['object_id'] = 'foreign'
    if fault == 'pin': next(p['pin'] for p in audit['meta']['registry_read_scope']['read_pins'] if p['collection']=='viewpoint_claim_links')[0] += 1
    if fault == 'statement': findings[0]['statement'] += 'x'
    if fault == 'source': audit['layers']['1']['findings'].append({'verdict':'source drift'})
    if fault == 'semantic': audit['layers']['3']['results'][0]['verdict'] = 'disputed'
    class ReadOnlyStore:
        def get_record_state(self, col, oid):
            value = deepcopy(store.get_record_state(col, oid))
            if fault == 'link' and col == 'viewpoint_claim_links': value['payload']['link_type'] = 'qualifies'
            return value
        def get_change_history(self, cid):
            value = deepcopy(store.get_change_history(cid))
            if fault == 'history': value['operations'][0]['before']['statement'] += 'x'
            if fault == 'write_set': value['operations'].append(deepcopy(value['operations'][0]))
            if fault == 'approval': value['metadata']['approval'] = {}
            return value
    if fault:
        with pytest.raises(ValueError):
            validate_claim_audit_report(audit, saved['claim_ids'], store=ReadOnlyStore(), saved=saved, root=root)
    else:
        accounting = validate_claim_audit_report(audit, saved['claim_ids'], store=ReadOnlyStore(), saved=saved, root=root)
        assert len(accounting['pending_owned_link_keys']) == 2
        assert accounting['original_findings'] == findings
        assert accounting['cvp_content_approved'] is False and accounting['group_complete'] is False
        assert audit == old_audit
        # The ordinary validator continues to reject this untouched CVP issue.
        with pytest.raises(ValueError, match='component_locator_findings'):
            validate_transaction_audit(audit, saved['claim_ids'], [])
    assert store.get_cvp_group(run) == original
    assert files == {p:c.file_sha256(p) for p in root.rglob('*') if p.is_file()}
