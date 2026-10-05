from pathlib import Path
import plistlib
import pytest
from backend.pipeline import viewpoint_backup_archive as m


@pytest.fixture
def archive(monkeypatch,tmp_path):
    local=tmp_path/'local';mount=tmp_path/'ssd';dest=mount/'archive'
    local.mkdir();mount.mkdir()
    monkeypatch.setattr(m,'LOCAL',local);monkeypatch.setattr(m,'MOUNT',mount);monkeypatch.setattr(m,'DEST',dest)
    monkeypatch.setattr(m,'verify_volume',lambda:{'volume_uuid':m.UUID})
    monkeypatch.setattr(m,'catalog',lambda p:m.digest(p))
    p=local/'registry-backup-2026-10-05-before-cvp-357-test'/m.NAME
    p.parent.mkdir();p.write_bytes(b'exact complete backup')
    return p,dest


def test_move_preserves_old_physical_sha_and_logical_binding(archive):
    from backend.pipeline.viewpoint_cvp_campaign_runner import binding
    p,dest=archive;before=binding(p);r=m.migrate_one(p)
    assert p.is_symlink() and binding(p)==before
    assert Path(r['archive_path']).read_bytes()==b'exact complete backup'
    assert r['file_sha256']==before['file_sha256']
    assert (p.parent/'ssd-archive-cutover.json').is_file()


@pytest.mark.parametrize('mutation',['missing','content','pointer','receipt','unmounted'])
def test_invalid_archive_never_consumes_old_proof(archive,monkeypatch,mutation):
    p,dest=archive;r=m.migrate_one(p);target=Path(r['archive_path'])
    if mutation=='missing':target.unlink()
    elif mutation=='content':target.write_bytes(b'drift')
    elif mutation=='pointer':p.unlink();p.symlink_to(dest/'foreign')
    elif mutation=='receipt':(p.parent/m.RECEIPT).write_text('{}')
    else:
        def unavailable():raise ValueError('unmounted')
        monkeypatch.setattr(m,'verify_volume',unavailable)
    with pytest.raises(ValueError):m.logical_binding_path(p)


@pytest.mark.parametrize('failure',['copy','restore','existing'])
def test_failure_keeps_complete_local_backup(archive,monkeypatch,failure):
    p,dest=archive
    if failure=='copy':monkeypatch.setattr(m.shutil,'copy2',lambda src,dst:Path(dst).write_bytes(b'drift'))
    elif failure=='restore':
        def bad(p):raise ValueError('unreadable catalog')
        monkeypatch.setattr(m,'catalog',bad)
    else:(dest/p.parent.name).mkdir(parents=True)
    with pytest.raises(ValueError):m.migrate_one(p)
    assert not p.is_symlink() and p.read_bytes()==b'exact complete backup'


def test_other_ticket_and_nonbackup_paths_unchanged(archive):
    p,dest=archive
    foreign=p.parent.parent/'registry-backup-2026-10-05-before-358'/m.NAME
    foreign.parent.mkdir();foreign.write_bytes(b'other task')
    with pytest.raises(ValueError):m.migrate_one(foreign)
    assert m.logical_binding_path(foreign)==foreign.resolve()
    assert not foreign.is_symlink()


def test_inventory_keeps_latest_two_and_excludes_other_tasks(archive):
    p,dest=archive
    for i in range(3):
        q=p.parent.parent/f'registry-backup-2026-10-05-before-cvp-357-{i}'/m.NAME
        q.parent.mkdir();q.write_bytes(b'backup')
    foreign=p.parent.parent/'registry-backup-2026-10-05-before-358'/m.NAME
    foreign.parent.mkdir();foreign.write_bytes(b'other')
    move,keep=m.inventory()
    assert len(move)==2 and len(keep)==2 and foreign not in move+keep


def test_volume_identity_rejects_wrong_disk(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'MOUNT',tmp_path);monkeypatch.setattr(m.os.path,'ismount',lambda p:True)
    monkeypatch.setattr(m.subprocess,'check_output',lambda argv:plistlib.dumps({'VolumeUUID':'wrong','MountPoint':str(tmp_path)}))
    with pytest.raises(ValueError,match='identity'):m.verify_volume()
