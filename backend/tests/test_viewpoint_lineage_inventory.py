import json

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_lineage_inventory import build_lineage_inventory
from backend.pipeline.viewpoint_lineage_inventory import discover_review_inputs


def test_applied_review_discovery_requires_exact_event_operation_and_unique_reason(tmp_path):
    from copy import deepcopy
    from backend.pipeline.viewpoint_lineage_inventory import applied_review_match
    from backend.pipeline.ai_review_ledger import Verdict, review_reason
    from backend.api.canonical_repository.postgres_store import record_content_sha
    import hashlib
    package = tmp_path/'input.json'; package.write_text('{}')
    pb = {'path':str(package), 'file_sha256':hashlib.sha256(package.read_bytes()).hexdigest()}
    variants = []
    for index in range(2):
        p = tmp_path/f'review{index}.json'
        p.write_text(json.dumps({'source':{'package_sha256':pb['file_sha256']},
           'claim_reviews':[{'claim_id':'C1','decision':'pass','rationale':f'reason{index}',
             'issues':[],'routing_status':'ai_reviewed'}]}))
        variants.append({'review_binding':{'path':str(p),'file_sha256':hashlib.sha256(p.read_bytes()).hexdigest()},
                         'review_input_binding':pb})
    before = {'claim_id':'C1','statement':'same','revision':2,'review_status':'candidate'}
    rb = variants[0]['review_binding']
    reason = review_reason(Verdict('C1','pass','ai_consensus_reviewed','reason0',rb['path'],rb['file_sha256']))
    after = before | {'revision':3,'review_status':'ai_consensus_reviewed',
                      'review_note':reason,'reviewed_by':'independent_ai_review'}
    event = {'event_id':'R','change_set_id':'K','source_kind':'review_decision','status':'applied',
       'reviewer_kind':'ai','reviewer_id':'independent_ai_review','decision':'ai_consensus_reviewed',
       'reason':reason,'artifact':{'change_set_id':'K'},'metadata':{'review_event_id':'R'},
       'before':before,'after':after,'before_sha':record_content_sha(before),
       'after_sha':record_content_sha(after),'source_sha':record_content_sha(before),
       'fingerprint':sha256_json({'review_event_id':'R','collection':'claims','object_id':'C1'}),
       'operation':'update','operation_count':1}
    assert applied_review_match('C1',variants,[event]) == variants[:1]
    for key,value in [('status','failed'),('reviewer_kind','human'),('source_kind','other'),
                      ('reason','prefix alone'),('metadata',{}),('artifact',{}),
                      ('fingerprint','wrong'),('before_sha','wrong'),('after_sha','wrong'),
                      ('source_sha','wrong'),('operation','create'),('operation_count',2)]:
        broken = deepcopy(event); broken[key]=value
        assert applied_review_match('C1',variants,[broken]) == variants
    broken = deepcopy(event);broken['after']['statement']='changed'
    broken['after_sha']=record_content_sha(broken['after'])
    assert applied_review_match('C1',variants,[broken]) == variants
    # Two distinct applied reasons stay ambiguous; neither date nor pass wins.
    second = deepcopy(event); rb=variants[1]['review_binding']
    second['reason']=review_reason(Verdict('C1','pass','ai_consensus_reviewed','reason1',rb['path'],rb['file_sha256']))
    second['after']['review_note']=second['reason'];second['after_sha']=record_content_sha(second['after'])
    assert applied_review_match('C1',variants,[event,second]) == variants
    # Physical SHA drift cannot be repaired by an apparently matching note.
    tmp_path.joinpath('review0.json').write_text('{}')
    assert applied_review_match('C1',variants,[event]) == variants


def test_later_review_input_discovery_matches_current_not_old_candidate(tmp_path):
    import hashlib
    from copy import deepcopy
    from backend.pipeline.viewpoint_cvp_campaign_runner import discover_selection
    batch = tmp_path / 'batch'
    (batch / 'reviews/review-generations').mkdir(parents=True)
    candidate = batch / 'reviewed/old.reviewed-candidate.json'
    current = {'claim_id': 'C1', 'statement': '新主張', 'claim_type': 'teaching', 'revision': 3}
    package = batch / 'input.json'
    package.write_text(json.dumps({'claims': [current]}))
    review = {'source': {'package_path': str(package),
        'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest()},
        'claim_reviews': [{'claim_id': 'C1', 'decision': 'pass'}]}
    p = batch / 'reviews/new.independent-review.json'
    p.write_text(json.dumps(review))
    (batch / 'reviews/review-generations/copy.json').write_bytes(p.read_bytes())
    class Store:
        def list_records(self, collection):
            assert collection == 'claims'
            return [current]
    index = {'C1': {'variants': [{'path': str(candidate),
        'changed_substantive_fields': ['statement']}]}}
    before = deepcopy(index)
    found = discover_review_inputs(claim_ids={'C1'}, inventory=index, store=Store())
    assert index == before and len(found['C1']['variants']) == 1
    selection, sealed = discover_selection({'claim_ids': ['C1']}, found)
    assert selection[0]['review_input_binding']['path'] == str(package)
    assert not sealed and 'legacy_reviewed_candidate_binding' not in selection[0]
    # A matching filename or pass is insufficient: changed physical input SHA
    # and changed current content each leave the old unqualified generation.
    package.write_text(json.dumps({'claims': [current]}, indent=2))
    assert discover_review_inputs(claim_ids={'C1'}, inventory=index, store=Store()) == index
    review['source']['package_sha256'] = hashlib.sha256(package.read_bytes()).hexdigest()
    p.write_text(json.dumps(review))
    current['statement'] = '第三代主張'
    assert discover_review_inputs(claim_ids={'C1'}, inventory=index, store=Store()) == index


def test_distinct_later_reviews_remain_ambiguous_not_selected_by_filename(tmp_path):
    # Discovery must never silently choose the newest file when decisions differ.
    import hashlib
    from backend.pipeline.viewpoint_cvp_campaign_runner import discover_selection
    import pytest
    (tmp_path / 'reviews').mkdir()
    live = {'claim_id': 'C1', 'statement': '主張', 'claim_type': 'teaching'}
    package = tmp_path / 'input.json'; package.write_text(json.dumps({'claims': [live]}))
    for i, decision in enumerate(['pass', 'changes_suggested']):
        (tmp_path / f'reviews/{i}.json').write_text(json.dumps({
            'source': {'package_path': str(package), 'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest()},
            'claim_reviews': [{'claim_id': 'C1', 'decision': decision}]}))
    class Store:
        def list_records(self, collection): return [live]
    index = {'C1': {'variants': [{'path': str(tmp_path / 'reviewed/old.json'), 'changed_substantive_fields': ['statement']}]}}
    found = discover_review_inputs(claim_ids={'C1'}, inventory=index, store=Store())
    with pytest.raises(ValueError, match='found 2'):
        discover_selection({'claim_ids': ['C1']}, found)


@__import__('pytest').mark.parametrize('mutation',[None,'missing','ambiguous','other_name','relative','tamper'])
def test_archived_review_input_requires_unique_full_sha(tmp_path,mutation):
    import hashlib
    from backend.pipeline.viewpoint_lineage_inventory import bound_review_input
    p=tmp_path/'batch.cross-section.json';p.write_text('new')
    g=tmp_path/'generations';g.mkdir();a=g/'batch.cross-section.old.json';a.write_text('reviewed')
    sha=hashlib.sha256(a.read_bytes()).hexdigest()
    if mutation=='missing':a.unlink()
    if mutation=='ambiguous':(g/'batch.cross-section.copy.json').write_text('reviewed')
    if mutation=='other_name':a.rename(g/'other.old.json')
    if mutation=='tamper':a.write_text('changed')
    review={'source':{'package_path':'batch.json' if mutation=='relative' else str(p),'package_sha256':sha}}
    assert bound_review_input(review)==(None if mutation else a)


def test_inventory_distinguishes_coordinate_changes_from_review_metadata(tmp_path):
    prior = {"claim_id": "C1", "statement": "原主張", "claim_type": "teaching",
             "occurrences": [{"anchors": [{"paragraph_key": "S0001"}]}]}
    (tmp_path / "one.reviewed-candidate.json").write_text(json.dumps({"claims": [prior]}))
    live = dict(prior, revision=2, review_status="ai_consensus_reviewed")

    class Store:
        def list_records(self, collection):
            assert collection == "claims"
            return [live]

    manifest = {"claim_packets": [{"claim_id": "C1"}]}
    manifest["artifact_sha256"] = sha256_json(manifest)
    inventory = build_lineage_inventory(manifest=manifest, reviewed_root=tmp_path, store=Store())
    assert inventory["claims"][0]["disposition"] == "historical_current_substance_match_requires_proof_adapter"
    assert inventory["eligibility_attestation_created"] is False
    assert inventory["master_data_mutations"] == 0
    live["occurrences"] = [{"anchors": [{"paragraph_key": "S0002"}]}]
    inventory = build_lineage_inventory(manifest=manifest, reviewed_root=tmp_path, store=Store())
    assert inventory["claims"][0]["disposition"] == "occurrence_change_requires_applied_repair_proof"
    assert inventory["claims"][0]["variants"][0]["changed_substantive_fields"] == ["occurrences"]
