from pathlib import Path
import re
from backend.pipeline.viewpoint_cvp_campaign_runner import campaign_backup_label


def test_repeated_recovery_root_fits_real_backup_script_contract():
    root=Path('/tmp/matthew-full-20261002-v7-recovery-5cace1dfce87-recovery-c99326d519c7')
    label=campaign_backup_label(root)
    assert re.fullmatch(r'[a-z0-9][a-z0-9-]{0,60}',label)
    assert len(label)<=61
    assert campaign_backup_label(root)==label


def test_backup_identity_is_complete_path_not_truncated_basename():
    a=Path('/tmp/a')/('same-prefix-'+'a'*90)
    b=Path('/tmp/b')/a.name
    assert campaign_backup_label(a)!=campaign_backup_label(b)
    assert campaign_backup_label(a)!=campaign_backup_label(a.with_name(a.name+'-2'))
