"""Cooperative campaign stop: never interrupt an in-flight model/write.

The runner checks before generation and after a fully audited group. SIGTERM
and SIGINT request that boundary rather than killing a child mid-response.
An uncatchable process/host failure is still covered by model call intents.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import signal
import threading

_control = ContextVar('cvp_campaign_control', default=None)


@contextmanager
def graceful_campaign_stop(root):
    control = {'root': Path(root).resolve(), 'signal': None}
    if _control.get() is not None:
        raise ValueError('one campaign stop owner per runtime')
    token = _control.set(control)
    previous = {}

    def request(signum, frame):
        # No I/O or exception in the handler. Let the current group finish.
        control['signal'] = signal.Signals(signum).name

    try:
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous[signum] = signal.signal(signum, request)
        yield control
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        _control.reset(token)


def stop_requested(root):
    root = Path(root).resolve()
    control = _control.get()
    return bool((control and control['root'] == root and control['signal'])
                or (root / 'stop-request.json').exists())


def main(argv=None):
    import argparse
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign-root', type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.campaign_root.resolve(strict=True)
    if not (root / 'campaign.json').is_file():
        raise ValueError('stop request requires an existing campaign')
    write_immutable(root / 'stop-request.json', {
        'schema_version': 'wang_cvp_campaign_stop_request_v1',
        'campaign_root': str(root), 'stop_at': 'before_generation_or_after_complete_group',
        'no_process_kill': True,
    })


if __name__ == '__main__':
    main()
