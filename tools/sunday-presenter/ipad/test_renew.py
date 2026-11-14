import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

spec = importlib.util.spec_from_file_location('renew', Path(__file__).with_name('renew.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
NOW = dt.datetime(2026, 11, 4, 20, tzinfo=dt.timezone.utc).timestamp()


class RenewalTests(unittest.TestCase):
    def test_lock_boot_and_expiry_guards(self):
        self.assertEqual(r.eligible(NOW, True, True, False, 0), 'wait_device')
        self.assertEqual(r.eligible(NOW, False, True, True, 0, True), 'wait_locked_after_first_unlock')
        self.assertEqual(r.eligible(NOW, True, False, True, 0), 'wait_locked_after_first_unlock')
        self.assertEqual(r.eligible(NOW, True, True, True, NOW+5*r.DAY), 'not_due')
        self.assertEqual(r.eligible(NOW, True, True, True, NOW+3*r.DAY), 'due')

    def test_sunday_uses_chicago_including_utc_monday(self):
        sunday = dt.datetime(2026, 11, 9, 2, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(r.eligible(sunday, True, True, True, 0, True), 'skip_sunday')

    def test_unchanged_or_wrong_device_profile_is_never_renewal(self):
        cfg = {'team': 'ABCDEFGHIJ', 'udid': 'one'}
        p = dict(team=cfg['team'], devices=['one'], app=cfg['team']+'.'+r.BUNDLE,
                 uuid='new', expires=NOW+7*r.DAY)
        self.assertTrue(r.extended(p, NOW+6*r.DAY, cfg, NOW))
        self.assertFalse(r.extended(p, p['expires'], cfg, NOW))
        self.assertFalse(r.extended(dict(p, devices=['other']), 0, cfg, NOW))
        self.assertFalse(r.extended(dict(p, app='other'), 0, cfg, NOW))
        self.assertFalse(r.extended(dict(p, expires=NOW+3*r.DAY), 0, cfg, NOW))

    def scenario(self, before=(True, True, True), after=(True, True, True), install=True):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        cfg = dict(root=str(root), volume=str(root), state=str(root/'state.json'),
                   developer_dir='/Applications/Xcode.app/Contents/Developer', team='ABCDEFGHIJ',
                   udid='one', manifest={})
        old = NOW+2*r.DAY
        r.save(cfg['state'], {'installed_expiry': old})
        calls = []

        class FakeRunner:
            def __init__(self, cfg):
                self.root = root
                self.app = root/'missing.app'
                self.states = iter([before, after])
            def safe_device(self): return next(self.states)
            def build(self, state, manual_retry=False):
                calls.append('build')
                return {'expires': NOW+7*r.DAY, 'uuid': 'new'}
            def device(self, *args):
                calls.append('install')
                if not install: raise RuntimeError('install_failed')
                return {'installedApplications': [{'bundleID': r.BUNDLE}]}

        with patch.object(r, 'stamp', return_value=NOW), patch.object(Path, 'is_mount', return_value=True), \
             patch.object(r, 'Runner', FakeRunner), patch.object(r.shutil, 'disk_usage') as usage:
            usage.return_value.free = 10*1024**3
            result = r.run(cfg)
        return old, result, calls

    def test_offline_and_unlocked_never_build_or_install(self):
        for before in [(False, False, False), (True, False, True)]:
            old, result, calls = self.scenario(before=before)
            self.assertEqual(calls, [])
            self.assertEqual(result['installed_expiry'], old)

    def test_device_unlocks_during_build_no_install(self):
        old, result, calls = self.scenario(after=(True, False, True))
        self.assertEqual(calls, ['build'])
        self.assertEqual(result['installed_expiry'], old)
        self.assertEqual(result['status'], 'wait_locked_before_install')

    def test_failed_install_never_advances_installed_expiry(self):
        old, result, calls = self.scenario(install=False)
        self.assertEqual(calls, ['build', 'install'])
        self.assertEqual(result['installed_expiry'], old)
        self.assertEqual(result['status'], 'needs_attention')

    def test_only_successful_install_advances_expiry(self):
        old, result, calls = self.scenario()
        self.assertEqual(result['installed_expiry'], NOW+7*r.DAY)
        self.assertEqual(result['status'], 'installed')

    def test_build_failure_restores_only_matching_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cache = home/'Library/Developer/Xcode/UserData/Provisioning Profiles'
            cache.mkdir(parents=True)
            own = cache/'own.mobileprovision'; own.write_bytes(b'original')
            other = cache/'other.mobileprovision'; other.write_bytes(b'other-app')
            cfg = dict(root=str(home), state=str(home/'state.json'), team='ABCDEFGHIJ',
                       udid='one', developer_dir='/Applications/Xcode.app/Contents/Developer')
            def fakeprofile(path):
                return dict(team=cfg['team'], devices=['one'],
                            app=cfg['team']+'.'+(r.BUNDLE if path==own else 'other'))
            with patch.object(Path, 'home', return_value=home), patch.object(r, 'profile', side_effect=fakeprofile), \
                 patch.object(r.Runner, 'command', side_effect=RuntimeError('build_failed')):
                with self.assertRaises(RuntimeError): r.Runner(cfg).build({})
            self.assertEqual(own.read_bytes(), b'original')
            self.assertEqual(other.read_bytes(), b'other-app')

    def test_timeout_cannot_wait_forever_for_keychain(self):
        with tempfile.TemporaryDirectory() as directory:
            runner = r.Runner(dict(root=directory, developer_dir='/Applications/Xcode.app/Contents/Developer'))
            with self.assertRaisesRegex(RuntimeError, 'timeout'):
                runner.command([sys.executable, '-c', 'import time; time.sleep(30)'], 'blocked', timeout=0.05)

    def test_background_build_is_throttled_but_explicit_manual_test_can_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = dict(root=directory, state=str(root/'state.json'), udid='one',
                       team='ABCDEFGHIJ', developer_dir='/Applications/Xcode.app/Contents/Developer')
            runner = r.Runner(cfg)
            with patch.object(r, 'stamp', return_value=NOW), patch.object(Path, 'home', return_value=root), \
                 patch.object(runner, 'command', side_effect=RuntimeError('attempted')) as command:
                with self.assertRaisesRegex(RuntimeError, 'throttled'):
                    runner.build({'last_build_attempt': NOW-60})
                command.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, 'attempted'):
                    runner.build({'last_build_attempt': NOW-60}, manual_retry=True)
                self.assertIn('generic/platform=iOS', command.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
