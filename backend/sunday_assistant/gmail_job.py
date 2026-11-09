"""Independent bounded mail polling so a slow mailbox cannot stall WhatsApp."""
from __future__ import annotations
import threading

from .gmail_inbox import GmailInbox
from .store import now_text


def start_mail_worker(agent):
    def run():
        inbox = None
        while True:
            try:
                agent.store.put('gmail_worker', {'status': 'processing', 'at': now_text()})
                if inbox is None:
                    inbox = GmailInbox(agent)
                inbox.poll()
                agent.store.put('gmail_worker', {'status': 'idle', 'at': now_text()})
            except BlockingIOError:
                pass  # A website review owns the intake lock; try on the next pass.
            except Exception as exc:
                from .task_audit import exception_detail
                agent.store.put('gmail_worker', {'status': 'error', 'at': now_text(), **exception_detail(exc), 'stage': 'poll_mailbox'})
            threading.Event().wait(15)
    thread = threading.Thread(target=run, name='sunday-speaker-mail', daemon=True)
    thread.start()
    return thread
