"""Historical accounting runs exact committed validators, never new calls."""
import hashlib,json,subprocess
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm,claim_historical_stage as historical
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.source_reconciliation_runner import write

@pytest.fixture
def archived_call(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir();files={
        'backend/__init__.py':'', 'backend/pipeline/__init__.py':'',
        'backend/pipeline/claim_management.py':'''class Policy:
 @staticmethod
 def model_validate(value):return value
def model_packet(materials):return materials['packet']
''',
        'backend/pipeline/source_reconciliation_runner.py':'''import json
from pathlib import Path
def read(path):return json.loads(Path(path).read_text())
def body(value):return {k:v for k,v in value.items() if k!="artifact_sha256"}
''',
        'backend/pipeline/cvp_sql_actions.py':'''from .source_reconciliation_runner import read
def validate_claim_stage(root,role,policy,packet):
 value=read(root/(role+'.raw.json'))
 assert value['provider_raw']=='validated original transport'
 return value
def claim_management_review_packet(materials,candidate):raise AssertionError('not this stage')
''', 'scripts/placeholder.txt':'read-only verification', 'config/placeholder.txt':'original config'}
    for name,content in files.items():
        path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
    def git(*args):return subprocess.check_output(['git',*args],cwd=repo,stderr=subprocess.DEVNULL,text=True).strip()
    git('init');git('add','.');git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Original validator')
    commit=git('rev-parse','HEAD');contract={name:hashlib.sha256((repo/name).read_bytes()).hexdigest() for name in files if name.startswith('backend/')}
    monkeypatch.setattr(historical,'KNOWN_IMPLEMENTATIONS',{cm.sha256_json(contract):commit})
    monkeypatch.setattr(historical,'__file__',str(repo/'backend/pipeline/claim_historical_stage.py'))
    root=tmp_path/'stage';root.mkdir();packet={'frozen':'original'};raw={'provider_raw':'validated original transport','request_payload_sha256':cm.sha256_json(packet),'response_sha256':'rejected-original-response'}
    materials={'round_id':'R','audit_contract':{'implementation':contract},'policy':{},'packet':packet}
    for name,value in [('materials',materials),('gpt.packet',packet),('gpt.raw',raw)]:write(root/(name+'.json'),value)
    raw=json.loads((root/'gpt.raw.json').read_text())
    proof={'original_commit':commit,'raw':binding(root/'gpt.raw.json'),'implementation_matches_entire_saved_contract':contract,
        'verification':{'authenticated':True,'round_id':'R','role':'gpt','request_payload_sha256':raw['request_payload_sha256'],'response_sha256':raw['response_sha256']}}
    path=tmp_path/'proof.json';path.write_text(json.dumps(proof));return materials,root,path,proof,raw

@pytest.mark.parametrize('fault',[None,'unknown_commit','implementation','raw','packet','report','original_validator'])
def test_exact_historical_replay_and_counterexamples(archived_call,fault):
    materials,root,path,proof,raw=archived_call
    if fault=='unknown_commit':proof['original_commit']='0'*40
    if fault=='implementation':proof['implementation_matches_entire_saved_contract']={}
    if fault=='raw':proof['raw']['file_sha256']='0'*64
    if fault=='packet':(root/'gpt.packet.json').write_text('{"changed":true}')
    if fault=='report':proof['verification']['response_sha256']='different answer'
    if fault=='original_validator':
        raw['provider_raw']='invalid transport';(root/'gpt.raw.json').unlink();write(root/'gpt.raw.json',{k:v for k,v in raw.items() if k!='artifact_sha256'});proof['raw']=binding(root/'gpt.raw.json')
    path.write_text(json.dumps(proof))
    if fault:
        with pytest.raises((ValueError,subprocess.CalledProcessError)):historical.verify(materials,root,'gpt',binding(path))
    else:
        assert historical.verify(materials,root,'gpt',binding(path))==raw
