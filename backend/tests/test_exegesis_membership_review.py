import hashlib
import importlib.util
import json
from pathlib import Path

spec=importlib.util.spec_from_file_location('membership_review',Path(__file__).parents[2]/'scripts/review-exegesis-membership.py')
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)


def test_independent_compiler_reopens_physical_source_and_preserves_svg_gap(tmp_path):
    file=tmp_path/'source.json';file.write_text(json.dumps({'script':[{'text':'# editorial heading'},{'text':'before<svg/>after'},{'text':'actual original'}]}))
    claim=dict(claim_id='C',claim_content_sha256='claimsha',source_id='S',evidence_steps=[dict(fragments=[dict(paragraph_key='S0002',verbatim_excerpt='not original')])])
    packet=tmp_path/'freeze.json';frozen=review.reader.seal(packet,dict(claims=[claim]))
    request=dict(stage='passage_membership',role_packet_path=str(packet),role_packet_sha256=frozen['artifact_sha256'],
        claims=[dict(id='c0001',original_claim_id='C',claim_content_sha256='claimsha',statement='summary')],
        sources=[dict(source_id='S',path=str(file),file_sha256=hashlib.sha256(file.read_bytes()).hexdigest())],
        targets=[dict(unit_id='u001')],catalog=[],context_radius=1)
    payload,index=review.compile_packet(request)
    assert payload['physical_sources'][0]['fragment_locations'][0]['physical_locations']==['row:3']
    assert index[('S','row:2')]==['before','after']
    assert index[('S','row:3')]==['actual original']
    assert '<svg' not in json.dumps(payload)
    item=dict(status='verified',primary='Matt.16.19',reason='source',evidence=[dict(source_id='S',location='row:3',quote='actual original')])
    finding=dict(status='pass',reason='boundary',evidence=item['evidence'],moves=[],suggested_passage_key=None,need_more_context='')
    response=dict(primary_reviews={'c0001':item},findings={'u001':finding})
    report=review.validate(response,request|{'artifact_sha256':'binding'},index)
    assert report['status']=='pass'
    response['primary_reviews']['c0001']['evidence']=[dict(source_id='S',location='row:2',quote='beforeafter')]
    assert review.validate(response,request|{'artifact_sha256':'binding'},index)['evidence_errors']


def test_schema_requires_each_claim_and_unit_explicitly():
    schema=review.schema_for(['c0001','c0002'],['u001','u002'])
    for field,keys in [('primary_reviews',['c0001','c0002']),('findings',['u001','u002'])]:
        assert schema['properties'][field]['required']==keys
        assert schema['properties'][field]['additionalProperties'] is False
