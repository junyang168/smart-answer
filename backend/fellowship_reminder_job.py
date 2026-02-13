from __future__ import annotations

import argparse
from email.utils import formataddr, parseaddr
import json
import os
import smtplib
import time as time_module
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from email.message import EmailMessage
from pathlib import Path
from typing import Iterable, Optional

from dotenv import load_dotenv
from zoneinfo import ZoneInfo

try:
    from .emailing import (
        chunked,
        determine_notification_recipients_file,
        determine_recipient_batch_size,
        is_truthy,
        load_notification_recipients,
        resolve_data_base_dir,
    )
except ImportError as exc:  # Allows running as a script (python fellowship_reminder_job.py)
    if __package__:
        raise

    import sys

    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))

    from emailing import (  # type: ignore[no-redef]
        chunked,
        determine_notification_recipients_file,
        determine_recipient_batch_size,
        is_truthy,
        load_notification_recipients,
        resolve_data_base_dir,
    )

TIMEZONE = ZoneInfo("America/Chicago")
SEND_TIME = time(8, 0)


RECIPIENT_BATCH_SIZE = determine_recipient_batch_size()

load_dotenv()

BASE_DIR = str(resolve_data_base_dir())


IS_PRODUCTION = is_truthy(os.getenv("PRODUCTION"))
IS_EMAIL_PRODUCTION = is_truthy(os.getenv("PRODUCTION"))
FALLBACK_TEST_RECIPIENT = "junyang168@gmail.com"

FELLOWSHIP_FILE = os.path.join(BASE_DIR, "config", "fellowship.json")
RECIPIENTS_FILE = determine_notification_recipients_file(IS_PRODUCTION, base_dir=Path(BASE_DIR))
print('production environment:', IS_PRODUCTION)
LOG_FILE = os.path.join(BASE_DIR , "notification", "notification.log")
STATUS_FILE = os.path.join(BASE_DIR , "notification", "last_sent.json")

@dataclass
class FellowshipEvent:
    date: date
    host: Optional[str] = None
    title: Optional[str] = None
    series: Optional[str] = None
    sequence: Optional[int] = None


def load_fellowship_events(path: str) -> list[FellowshipEvent]:
    path = Path(path)
    data = json.loads(path.read_text())
    if not isinstance(data, list) or not data:
        raise ValueError("fellowship.json must be a non-empty list of date objects")

    parsed: list[FellowshipEvent] = []
    for entry in data:
        if not isinstance(entry, dict) or "date" not in entry:
            raise ValueError("Each entry in fellowship.json must be an object with a 'date' field")
        try:
            event_date = datetime.strptime(entry["date"], "%m/%d/%Y").date()
        except ValueError as exc:
            raise ValueError(f"Invalid date format: {entry['date']}") from exc

        def _clean_optional_str(key: str) -> Optional[str]:
            value = entry.get(key)
            if value is None:
                return None
            if not isinstance(value, str):
                return str(value)
            value = value.strip()
            return value or None

        def _clean_optional_int(key: str) -> Optional[int]:
            value = entry.get(key)
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid integer for '{key}': {value}") from exc

        parsed.append(
            FellowshipEvent(
                date=event_date,
                host=_clean_optional_str("host"),
                title=_clean_optional_str("title"),
                series=_clean_optional_str("series"),
                sequence=_clean_optional_int("sequence"),
            )
        )
    return parsed


def _log_notification(message: str) -> None:
    if not LOG_FILE:
        return

    log_path = Path(LOG_FILE)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S%z")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        # Logging issues should not block email sending
        pass


def _load_last_sent_event_date() -> Optional[str]:
    if not STATUS_FILE:
        return None

    path = Path(STATUS_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        last_event = data.get("event_date")
        if isinstance(last_event, str):
            return last_event
    return None


def _record_event_sent(event: FellowshipEvent) -> None:
    if not STATUS_FILE:
        return

    path = Path(STATUS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_date": event.date.isoformat(),
        "recorded_at": datetime.now(TIMEZONE).isoformat(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def build_email_content(event: FellowshipEvent) -> tuple[str, str, str, str]:
    formatted_date = datetime.combine(event.date, time.min, tzinfo=TIMEZONE).strftime("%m/%d")
    raw_sender = os.getenv("REMINDER_SENDER") or os.getenv("SMTP_USERNAME")
    if not raw_sender:
        raise RuntimeError("REMINDER_SENDER or SMTP_USERNAME must be set in the environment")

    name_override = os.getenv("REMINDER_FROM_NAME")
    parsed_name, parsed_email = parseaddr(raw_sender)
    if not parsed_email:
        parsed_email = raw_sender
    display_name = name_override or parsed_name
    sender = formataddr((display_name, parsed_email)) if display_name else parsed_email

    subject = os.getenv("REMINDER_SUBJECT", f"圣道教会 {formatted_date} 周五团契 时间改為周五晚 7:30 - 9:00 CST ")
    details_lines: list[str] = []
    if event.host:
        details_lines.append(f"主持人: {event.host} ")
    if event.series:
        series_line = f"系列: {event.series} 系列"
        if event.sequence is not None:
            series_line += f" 的第 {event.sequence} 講"
        details_lines.append(series_line)
    if event.title:
        details_lines.append(f"主題: {event.title}")

    details_text = ""
    if details_lines:
        bullet_lines = "\n".join(f" - {line}" for line in details_lines)
        details_text = f"Event details:\n{bullet_lines}\n\n"

    default_body = (
        "Hi everyone,\n\n"
        "This is a friendly reminder that our next fellowship meets on {date}.\n"
        "{details}"
        "Please reach out if you have any questions.\n\n"
        "Grace and peace,\nYour ministry team"
    )
    body_template = os.getenv("REMINDER_BODY_TEMPLATE", default_body)
    text_body = body_template.format(
        date=formatted_date,
        details=details_text,
        host=event.host or "",
        title=event.title or "",
        series=event.series or "",
        sequence=event.sequence or "",
    )

    details_html = ""
    if details_lines:
        rows = []
        for line in details_lines:
            if ": " in line:
                label, value = line.split(": ", 1)
            else:
                label, value = line, ""
            rows.append(
                "<tr>"
                f"<td style=\"padding:4px 12px 4px 0;\">{label}:</td>"
                f"<td style=\"padding:4px 0;font-weight:600;\">{value}</td>"
                "</tr>"
            )
        details_html = (
            "<table style=\"border-collapse:collapse;margin:16px 0;\">"
            + "".join(rows)
            + "</table>"
        )

    default_html = (
        "<div style=\"font-family:Roboto,Helvetica,Arial,sans-serif;font-size:16px;color:#202124;\">"
        "  <p style=\"margin:0 0 16px 0;\">弟兄姊妹们平安，</p>"
        "  <p style=\"margin:0 0 16px 0;\">"
        "    圣道教会每两周一次的团契自本週 <strong> {date} </strong>起將改為線上進行。时间改為周五晚 7:30 - 9:00 CST。欢迎大家参加。<br/>"
        "  </p>"
        "  {details_html}"
        "</div>"
        "<div>"
        "    <a href=\"Https://us02web.zoom.us/j/85114274206?pwd=ZUwq4UzMMH9XmJlkN7fjULnFyKeaVq.1\">Zoom 線上會議</a>"
        "</div>"
        "<div><br/><br/>"
        "<a href=\"https://www.dallas-hlc.org/resources/articles\">觀看過往團契分享</a>"
        "</div>"
    )
    html_body_template = os.getenv("REMINDER_BODY_TEMPLATE_HTML", default_html)
    html_body = html_body_template.format(
        date=formatted_date,
        details_html=details_html,
        host=event.host or "",
        title=event.title or "",
        series=event.series or "",
        sequence=event.sequence or "",
    )

    return sender, subject, text_body, html_body


def send_email(recipients: Iterable[str], event: FellowshipEvent) -> None:
    sender, subject, text_body, html_body = build_email_content(event)
    recipient_list = list(recipients)
    if not IS_EMAIL_PRODUCTION:
        recipient_list = [FALLBACK_TEST_RECIPIENT]
        print("IPRODUCTION is false; overriding recipients to test address.")
    if not recipient_list:
        return

    host = os.getenv("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST environment variable is required")

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}

    to_header = os.getenv("REMINDER_TO_HEADER", "Undisclosed recipients:;")
    cc_header = os.getenv("REMINDER_CC_HEADER")

    batches_sent = 0
    total_sent = 0

    try:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            for index, batch in enumerate(chunked(recipient_list, RECIPIENT_BATCH_SIZE), start=1):
                message = EmailMessage()
                message["From"] = sender
                message["To"] = to_header
                if cc_header:
                    message["Cc"] = cc_header
                message["Subject"] = subject
                message.set_content(text_body)
                message.add_alternative(html_body, subtype="html")
                smtp.send_message(message, to_addrs=batch)
                batches_sent = index
                total_sent += len(batch)
                if index * RECIPIENT_BATCH_SIZE < len(recipient_list):
                    time_module.sleep(1)
    except Exception as exc:
        _log_notification(
            f"ERROR sending fellowship reminder for {event.date.isoformat()} to {len(recipient_list)} recipients: {exc}"
        )
        raise
    else:
        _log_notification(
            f"SUCCESS sent fellowship reminder for {event.date.isoformat()} to {total_sent} recipients in {batches_sent} batch(es)"
        )
        _record_event_sent(event)


def compute_send_timestamp(events: list[FellowshipEvent]) -> tuple[FellowshipEvent, datetime]:
    if not events:
        raise ValueError("No fellowship events available")

    # Sort events by date to ensure correctness regardless of JSON order
    sorted_events = sorted(events, key=lambda e: e.date)

    # Find the next event that is strictly in the future relative to "today".
    # Logic: We send the reminder 1 day before the event.
    # If today is (event.date - 1), then event.date > today.
    # If today is event.date, the window has passed (now.date() > send_at.date()).
    now_date = datetime.now(TIMEZONE).date()
    future_events = [e for e in sorted_events if e.date > now_date]

    if future_events:
        target_event = future_events[0]
    else:
        # Fallback to the absolute last event if no future events exist
        target_event = sorted_events[-1]

    send_date = target_event.date - timedelta(days=1)
    send_at = datetime.combine(send_date, SEND_TIME, tzinfo=TIMEZONE)
    return target_event, send_at


def main() -> None:
    parser = argparse.ArgumentParser(description="Send fellowship reminder emails.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send the reminder regardless of the computed schedule.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the email details without sending.",
    )
    args = parser.parse_args()

    events = load_fellowship_events(FELLOWSHIP_FILE)
    recipients = load_notification_recipients(RECIPIENTS_FILE)
    event, send_at = compute_send_timestamp(events)

    last_sent_event = _load_last_sent_event_date()
    event_date_iso = event.date.isoformat()

    print("Last scheduled fellowship date:", event.date.strftime("%Y-%m-%d"))
    if event.title:
        print("Title:", event.title)
    if event.series:
        series_line = event.series
        if event.sequence is not None:
            series_line += f" (Session {event.sequence})"
        print("Series:", series_line)
    elif event.sequence is not None:
        print("Session:", event.sequence)
    if event.host:
        print("Host:", event.host)
    print("Reminder target send time:", send_at.isoformat())

    now = datetime.now(TIMEZONE)
    print("Current time:", now.isoformat())
    if not args.force and last_sent_event == event_date_iso:
        print("Reminder already sent for this fellowship; exiting without sending.")
        return
    if not args.force:
        # Safety check: If the target event is in the past, it means we likely
        # exhausted our configuration (fallback to last event) or missed the window.
        if event.date < now.date():
            print(f"Configuration exhausted or stale. Last event was {event.date}. Exiting.")
            return

        if now < send_at:
            print(f"Not time yet. Scheduled for {send_at}. Exiting without sending.")
            return
        if now.date() > send_at.date():
            print("Send window has passed; exiting without sending.")
            return

    if args.dry_run:
        print("Dry run only. Would send to:", ", ".join(recipients))
        return
    send_email(recipients, event)


if __name__ == "__main__":
    main()
