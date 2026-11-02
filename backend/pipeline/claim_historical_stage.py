"""Explicit read-only replay of one returned/rejected historical Claim call.

Only accounting recovery uses this; never an approval or a live call adapter.
"""
import hashlib,io,json,os,re,subprocess,sys,tarfile,tempfile
from pathlib import Path

KNOWN_IMPLEMENTATIONS = {'23a0e1e99b7fd1f64041eb58bcde1b61cf62c2b4bd8deb86da3d15460e4e6b88': '1179f18262d55609d35c6e256776969a29a27d08', '779f0ba97169445cc8a7b4b82886cde66758466a621a526398def40a4286845a': '10a5e6e73fb6302e0328a8f4c58654cf9478d92c'}

def verify(materials, root, role, proof):
    repository = Path(__file__).resolve().parents[2]
    from backend.pipeline.cvp_sql_commit import check_binding,binding
    from backend.pipeline import claim_management as cm
    from backend.pipeline.source_reconciliation_runner import read,body
    check_binding(proof);record=json.loads(Path(proof['path']).read_text())
    commit=record.get('original_commit','')
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('historical stage requires an exact Git commit')
    root=Path(root);contract=materials['audit_contract']['implementation']
    if KNOWN_IMPLEMENTATIONS.get(cm.sha256_json(contract)) != commit:
        raise ValueError('historical stage implementation is not an explicitly supported original version')
    if record.get('raw')!=binding(root/(role+'.raw.json')) or record.get('implementation_matches_entire_saved_contract')!=contract:
        raise ValueError('historical stage original raw/implementation binding drift')
    # No arbitrary command, script or Python source comes from the proof file.
    for name,digest in contract.items():
        if not name.startswith(('backend/','scripts/')) or '..' in Path(name).parts:raise ValueError('invalid historical implementation path')
        code=subprocess.run(['git','show',commit+':'+name],cwd=repository,capture_output=True,check=True).stdout
        if hashlib.sha256(code).hexdigest()!=digest:raise ValueError('historical implementation differs from the complete frozen contract')
    packet_path=root/(role+'.packet.json');packet=body(read(packet_path))
    raw=read(root/(role+'.raw.json'))
    if cm.sha256_json(packet)!=raw['request_payload_sha256']:raise ValueError('historical packet binding drift')
    archive=subprocess.run(['git','archive',commit,'backend','scripts','config'],cwd=repository,capture_output=True,check=True).stdout
    script='''import json,sys
from pathlib import Path
from backend.pipeline import claim_management as cm
from backend.pipeline.source_reconciliation_runner import read,body
from backend.pipeline.cvp_sql_actions import validate_claim_stage,claim_management_review_packet
root=Path(sys.argv[1]);role=sys.argv[2];m=body(read(root/'materials.json'))
p=cm.model_packet(m) if role=='gpt' else claim_management_review_packet(m,cm.Candidate.model_validate(body(read(root/'candidate.json'))))
a=validate_claim_stage(root,role,cm.Policy.model_validate(m['policy']),p)
print(json.dumps({'authenticated':True,'round_id':m['round_id'],'role':role,'request_payload_sha256':a['request_payload_sha256'],'response_sha256':a['response_sha256']}))
'''
    with tempfile.TemporaryDirectory(prefix='claim-rejected-stage-') as directory:
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:source.extractall(directory,filter='data')
        environment=dict(os.environ,PYTHONPATH=directory,PYTHONDONTWRITEBYTECODE='1')
        out=subprocess.run([sys.executable,'-c',script,str(root),role],cwd=directory,env=environment,capture_output=True,text=True,timeout=60,check=True)
    expected={'authenticated':True,'round_id':materials['round_id'],'role':role,'request_payload_sha256':raw['request_payload_sha256'],'response_sha256':raw['response_sha256']}
    if json.loads(out.stdout)!=expected or record.get('verification')!=expected:raise ValueError('historical stage replay differs from the original call')
    check_binding(proof)
    return raw
