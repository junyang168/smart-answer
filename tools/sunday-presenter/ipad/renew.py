#!/usr/bin/python3
"""Owner-only Personal Team renewal. Uses Xcode/Keychain; stores no passwords.

The LaunchAgent retries availability, not authentication. Never runs on Sunday,
never installs on an unlocked iPad, and never claims an unchanged profile renewed.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
from zoneinfo import ZoneInfo

BUNDLE = 'org.dallas-hlc.sunday-remote.ipad'
LABEL = 'org.dallas-hlc.sunday-remote.renewal'
UTC = dt.timezone.utc
DAY = 86400


def stamp():
    return dt.datetime.now(UTC).timestamp()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(path)


def read(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else {}


def profile(path):
    data = subprocess.check_output(['/usr/bin/security', 'cms', '-D', '-i', str(path)], timeout=20)
    p = plistlib.loads(data)
    return {'uuid': p['UUID'], 'expires': p['ExpirationDate'].replace(tzinfo=UTC).timestamp(),
            'team': p['TeamIdentifier'][0], 'app': p['Entitlements']['application-identifier'],
            'devices': p.get('ProvisionedDevices', [])}


def matching(p, cfg):
    return (p['team'] == cfg['team'] and p['app'] == cfg['team'] + '.' + BUNDLE
            and cfg['udid'] in p['devices'])


def eligible(now, locked, since_boot, connected, last_expiry, force=False):
    if dt.datetime.fromtimestamp(now, ZoneInfo('America/Chicago')).weekday() == 6:
        return 'skip_sunday'
    if not connected:
        return 'wait_device'
    if not locked or not since_boot:
        return 'wait_locked_after_first_unlock'
    if not force and last_expiry - now > 4 * DAY:
        return 'not_due'
    return 'due'


def extended(new, old, cfg, now):
    return matching(new, cfg) and new['expires'] > max(old + 60, now + 4 * DAY)


class Runner:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = Path(cfg['root'])
        self.env = dict(os.environ, DEVELOPER_DIR=cfg['developer_dir'])

    def command(self, args, name, timeout=60):
        log = self.root / (name + '.log')
        # One current and one previous log per operation; no unbounded history.
        if log.exists():
            log.replace(log.with_suffix('.previous.log'))
        with log.open('wb') as out:
            log.chmod(0o600)
            proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT,
                                    env=self.env, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                raise RuntimeError(name + '_timeout_requires_inspection')
        if log.stat().st_size > 4 * 1024 * 1024:
            with log.open('rb') as f:
                f.seek(-4 * 1024 * 1024, 2)
                tail = f.read()
            log.write_bytes(tail)
        if code:
            raise RuntimeError(name + '_failed_see_local_log')

    def device(self, subcommand, name, extra=()):
        output = self.root / (name + '.json')
        output.unlink(missing_ok=True)
        self.command(['/usr/bin/xcrun', 'devicectl', 'device'] + subcommand +
                     ['--device', self.cfg['device'], '--timeout', '45',
                      '--json-output', str(output), '--quiet'] + list(extra), name)
        doc = read(output)
        if doc.get('info', {}).get('outcome') != 'success':
            raise RuntimeError(name + '_unverified_result')
        return doc['result']

    def safe_device(self):
        d = self.device(['info', 'details'], 'device')
        c = d.get('connectionProperties', {})
        if (d.get('hardwareProperties', {}).get('udid') != self.cfg['udid'] or
                c.get('pairingState') != 'paired' or c.get('tunnelState') != 'connected'):
            return False, False, False
        lock = self.device(['info', 'lockState'], 'lock')
        return True, lock.get('passcodeRequired') is True, lock.get('unlockedSinceBoot') is True

    def build(self, state, manual_retry=False):
        now = stamp()
        if not manual_retry and now - state.get('last_build_attempt', 0) < DAY:
            raise RuntimeError('build_throttled_until_next_day')
        state['last_build_attempt'] = now
        save(self.cfg['state'], state)
        cache = Path.home() / 'Library/Developer/Xcode/UserData/Provisioning Profiles'
        backup = self.root / 'profile-backup'
        backup.mkdir(exist_ok=True, mode=0o700)
        # Keep only the current attempt's backups; these are disposable profiles,
        # not private signing keys. Never revoke any certificate/server profile.
        for old in backup.glob('*.mobileprovision'):
            old.unlink()
        removed = []
        try:
            for path in cache.glob('*.mobileprovision'):
                p = profile(path)
                if matching(p, self.cfg):
                    target = backup / path.name
                    shutil.copy2(path, target)
                    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                        raise RuntimeError('profile_backup_mismatch')
                    path.unlink()
                    removed.append((path, target))
            self.command(['/usr/bin/xcodebuild', '-project', str(self.root / 'source/ipad/SundayRemote.xcodeproj'),
                          '-scheme', 'SundayRemote', '-configuration', 'Debug',
                          '-destination', 'generic/platform=iOS',
                          '-derivedDataPath', str(self.root / 'DerivedData'),
                          '-allowProvisioningUpdates', '-allowProvisioningDeviceRegistration',
                          'DEVELOPMENT_TEAM=' + self.cfg['team'], 'CODE_SIGN_STYLE=Automatic',
                          'CODE_SIGNING_ALLOWED=YES', 'build'], 'build', timeout=300)
            self.command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(self.app)], 'verify')
            p = profile(self.app / 'embedded.mobileprovision')
            if not extended(p, state.get('installed_expiry', 0), self.cfg, stamp()):
                raise RuntimeError('profile_did_not_extend_or_wrong_binding')
            return p
        finally:
            # Restore original local cache if Xcode did not recreate its UUID.
            # Existing installed app and signing key are never removed.
            for path, target in removed:
                if not path.exists():
                    shutil.copy2(target, path)

    @property
    def app(self):
        return self.root / 'DerivedData/Build/Products/Debug-iphoneos/SundayRemote.app'


def run(cfg, force=False):
    state = read(cfg['state'])
    now = stamp()
    if dt.datetime.fromtimestamp(now, ZoneInfo('America/Chicago')).weekday() == 6:
        return report(cfg, state, 'skip_sunday')
    volume = Path(cfg['volume'])
    if not volume.is_mount() or not Path(cfg['root']).is_dir():
        return report(cfg, state, 'wait_ssd')
    runner = Runner(cfg)
    try:
        connected, locked, booted = runner.safe_device()
    except (RuntimeError, subprocess.SubprocessError, OSError) as exc:
        return report(cfg, state, 'device_probe_failed', str(exc))
    decision = eligible(now, locked, booted, connected, state.get('installed_expiry', 0), force)
    if decision != 'due':
        return report(cfg, state, decision)
    try:
        for root, minimum in [(Path.home(), 3 * 1024**3), (volume, 5 * 1024**3)]:
            if shutil.disk_usage(root).free < minimum:
                return report(cfg, state, 'wait_free_space')
        for name, digest in cfg['manifest'].items():
            if hashlib.sha256((runner.root / 'source' / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError('source_snapshot_changed')
        p = None
        if runner.app.exists():
            candidate = profile(runner.app / 'embedded.mobileprovision')
            if extended(candidate, state.get('installed_expiry', 0), cfg, now):
                runner.command(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(runner.app)], 'verify')
                p = candidate
        if p is None:
            p = runner.build(state, manual_retry=force)
        # Recheck lock immediately before installation; never terminate the app
        # or launch it afterwards. A failed install reuses this verified artifact.
        connected, locked, booted = runner.safe_device()
        if eligible(stamp(), locked, booted, connected, 0, True) != 'due':
            return report(cfg, state, 'wait_locked_before_install')
        installed = runner.device(['install', 'app'], 'install', [str(runner.app)])
        if not any(app.get('bundleID') == BUNDLE for app in installed.get('installedApplications', [])):
            raise RuntimeError('install_result_missing_expected_app')
        state.update(previous_installed_expiry=state.get('installed_expiry'),
                     installed_expiry=p['expires'], installed_profile=p['uuid'],
                     last_install=stamp())
        return report(cfg, state, 'installed')
    except (RuntimeError, subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        return report(cfg, state, 'needs_attention', str(exc))


def report(cfg, state, status, error=None):
    state.update(last_check=stamp(), status=status)
    state.pop('error', None)
    if error:
        state['error'] = error
    save(cfg['state'], state)
    print(json.dumps(state))
    return state


def agent(cfg, config_path):
    path = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    path.parent.mkdir(exist_ok=True)
    payload = {'Label': LABEL, 'ProgramArguments': ['/usr/bin/python3', cfg['script'], '--config',
                str(config_path), 'run'], 'RunAtLoad': True, 'StartInterval': 3600,
               'ProcessType': 'Background', 'LowPriorityIO': True,
               'EnvironmentVariables': {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'},
               'StandardOutPath': str(Path(cfg['state']).with_name('agent.stdout.log')),
               'StandardErrorPath': str(Path(cfg['state']).with_name('agent.stderr.log'))}
    path.write_bytes(plistlib.dumps(payload))
    path.chmod(0o600)
    subprocess.run(['/bin/launchctl', 'bootout', 'gui/' + str(os.getuid()), str(path)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['/bin/launchctl', 'bootstrap', 'gui/' + str(os.getuid()), str(path)], check=True)
    print(path)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('mode', choices=['run', 'status', 'install-agent'])
    parser.add_argument('--force-due', action='store_true', help='Manual test only; lock/Sunday guards remain')
    args = parser.parse_args()
    cfg = read(args.config)
    # launchd keeps these files open in append mode; truncate in place instead
    # of renaming a live file descriptor. Device/build logs rotate separately.
    for suffix in ['stdout', 'stderr']:
        log = Path(cfg['state']).with_name('agent.' + suffix + '.log')
        if log.exists() and log.stat().st_size > 512 * 1024:
            with log.open('w'):
                pass
    if not re.fullmatch('[A-Z0-9]{10}', cfg['team']):
        raise ValueError('invalid_team')
    for name in ['root', 'volume', 'state', 'script', 'developer_dir']:
        if not Path(cfg[name]).is_absolute():
            raise ValueError('absolute_paths_required')
    if not Path(cfg['root']).is_relative_to(Path(cfg['volume'])):
        raise ValueError('build_root_outside_configured_volume')
    if args.mode == 'status':
        print(json.dumps(read(cfg['state']), indent=2))
    elif args.mode == 'install-agent':
        agent(cfg, args.config.resolve())
    else:
        lockpath = Path(cfg['state']).with_suffix('.lock')
        lockpath.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with lockpath.open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('another_run_active')
                return
            run(cfg, args.force_due)


if __name__ == '__main__':
    main()
