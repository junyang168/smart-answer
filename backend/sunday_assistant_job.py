"""Run the durable Sunday assistant worker; web restarts do not own its queue."""
from __future__ import annotations

import argparse
import logging
import time

from dotenv import load_dotenv

load_dotenv()


def main():
    from backend.sunday_assistant.engine import get_assistant

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    agent = get_assistant()
    if not args.once:
        from backend.sunday_assistant.gmail_job import start_mail_worker
        start_mail_worker(agent)
    while True:
        try:
            agent.tick()
        except Exception as exc:
            from backend.sunday_assistant.store import now_text
            agent.store.put("worker", {"at": now_text(), "status": "error", "error": str(exc)[:1000]})
            logging.exception("Sunday assistant worker failed; durable queue retained")
        if args.once:
            return
        time.sleep(10)


if __name__ == "__main__":
    main()
