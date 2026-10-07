from pathlib import Path
import json
import pytest
from backend.pipeline import viewpoint_backup_archive as a


@pytest.fixture
def archive(monkeypatch,tmp_path):
    local=tmp_path/'local';mount=tmp_path/'ssd';base=tmp_path/'cvp-production-357'
    for p in (local,mount,base):p.mkdir()
    monkeypatch.setattr(a,'LOCAL',local);monkeypatch.setattr(a,'MOUNT',mount)
    monkeypatch.setattr(a,'DEST',mount/'archive');monkeypatch.setattr(a,'BASE',base)
    monkeypatch.setattr(a,'verify_volume',lambda:{'volume_uuid':a.UUID})
    monkeypatch.setattr(a,'catalog',lambda p:a.digest(p))
    from types import SimpleNamespace
    monkeypatch.setattr(a.shutil,'disk_usage',lambda p:SimpleNamespace(free=10*1024**3))
    return local,mount,base


def test_unlabelled_backup_requires_exact_saved_restore_proof(archive):
    local,_,base=archive;p=local/'registry-backup-2026-10-07-before-source-test'/a.NAME
    p.parent.mkdir();p.write_bytes(b'backup')
    assert not a.eligible(p)
    proof=base/'backup.json';proof.write_text(json.dumps({'dump':{'path':str(p),'file_sha256':a.digest(p)},'restore_list_exit_code':0,'restore_catalog_nonempty':True}))
    a.authorize_backup(p,proof);before=a.digest(p);a.migrate_one(p)
    assert p.is_symlink() and a.logical_binding_path(p)==p and a.digest(p)==before
    from backend.pipeline.source_reconciliation_runner import binding
    assert binding(p)=={'path':str(p),'file_sha256':before}
    proof.write_text('{}')
    with pytest.raises(ValueError,match='scope drift'):a.logical_binding_path(p)


@pytest.mark.parametrize('bad',['sha','catalog','foreign'])
def test_invalid_explicit_proof_rejected(archive,bad):
    local,_,base=archive;p=local/'registry-backup-2026-10-07-before-source-test'/a.NAME
    p.parent.mkdir();p.write_bytes(b'backup')
    proof=(base if bad!='foreign' else base.parent)/'backup.json'
    proof.write_text(json.dumps({'dump':{'path':str(p),'file_sha256':'bad' if bad=='sha' else a.digest(p)},'restore_list_exit_code':0,'restore_catalog_nonempty':bad!='catalog'}))
    with pytest.raises(ValueError):a.authorize_backup(p,proof)
    assert not p.is_symlink()


def test_direct_backup_ssd_and_no_retry(archive,monkeypatch):
    local,mount,_=archive
    def dump(argv,**kwargs):Path(argv[2].split('=',1)[1]).write_bytes(b'complete dump')
    monkeypatch.setattr(a.subprocess,'run',dump)
    r=a.create_backup('before-source-357-test','not-a-real-db')
    assert r['direct_ssd'] and Path(r['original_path']).is_symlink()
    assert Path(r['archive_path']).is_relative_to(mount)
    with pytest.raises(ValueError,match='attempted'):a.create_backup('before-source-357-test','not-a-real-db')


def test_direct_backup_missing_ssd_no_local_fallback(archive,monkeypatch):
    local,_,_=archive
    def missing():raise ValueError('SSD missing')
    monkeypatch.setattr(a,'verify_volume',missing)
    with pytest.raises(ValueError,match='SSD missing'):a.create_backup('before-source-357-test','x')
    assert list(local.iterdir())==[]


def test_new_snapshots_share_exact_rows_old_files_unchanged(tmp_path,monkeypatch):
    from backend.pipeline import immutable_proof_reuse as p
    from backend.pipeline.source_reconciliation_runner import write,read
    monkeypatch.setattr(p,'MIN_SNAPSHOT_BYTES',1)
    root=tmp_path/'source-reconciliation';one=root/'a/frozen.json';two=root/'b/before.json'
    rows=[{'id':'中文','value':[1,2,3]}]
    write(one,{'before_records':rows});old=one.read_bytes();write(two,{'records':rows})
    assert read(one)['before_records']==rows and read(two)['records']==rows
    assert one.read_bytes()==old
    blobs=list((root/'immutable-snapshots').glob('*.rows.json'));assert len(blobs)==1
    blobs[0].write_bytes(b'[]')
    with pytest.raises(ValueError,match='SHA drift'):read(one)


def test_verified_history_reuses_only_physical_pins_and_sql_generation(tmp_path):
    from backend.pipeline import immutable_proof_reuse as p
    from backend.pipeline.source_reconciliation_runner import binding
    class Cursor:
        generation='1'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):pass
        def fetchone(self):return [self.generation]
        def cursor(self):return self
        def connect(self):return self
    store=Cursor();file=tmp_path/'proof.json';file.write_text('{}');bound=binding(file)
    calls=[];p._verified.clear()
    p.verify_once('x',[bound],store,lambda:calls.append(1))
    p.verify_once('x',[bound],store,lambda:calls.append(1));assert len(calls)==1
    store.generation='2';p.verify_once('x',[bound],store,lambda:calls.append(1));assert len(calls)==2
    file.write_text('{"drift":1}')
    with pytest.raises(ValueError):p.verify_once('x',[bound],store,lambda:calls.append(1))
    assert len(calls)==2


def test_missing_normal_answer_never_retries_even_with_fixed_runtime(tmp_path):
    from backend.pipeline.owner_approved_link_correction import initialize_normal_runtime
    review=tmp_path/'review';review.mkdir();intent=review/'raw-proposal.call-intent.json'
    intent.write_text('{"once":true}');original=intent.read_bytes()
    calls=[]
    with pytest.raises(ValueError,match='outcome_unknown'):
        initialize_normal_runtime(tmp_path,lambda:calls.append(1))
    assert not calls and intent.read_bytes()==original


def test_normal_runtime_authenticated_without_diagnostic_before_new_intent(tmp_path):
    from backend.pipeline.owner_approved_link_correction import initialize_normal_runtime
    proof={'reused':True,'new_model_calls':0,'proof_path':'existing'}
    assert initialize_normal_runtime(tmp_path,lambda:proof)==proof
    assert (tmp_path/'runtime-proof.json').exists()
    assert not (tmp_path/'review').exists()
