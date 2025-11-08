from __future__ import annotations

import html
import os
import re
import smtplib
from collections import OrderedDict
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from backend.emailing import (
    chunked,
    determine_notification_recipients_file,
    determine_recipient_batch_size,
    is_truthy,
    load_notification_recipients,
    resolve_data_base_dir,
)
from .config import SUNDAY_SERVICE_FILE, SUNDAY_WORKERS_FILE, SUNDAY_WORSHIP_DIR
from .models import (
    SundayServiceEmailResult,
    SundayServiceEntry,
    SundayWorker,
)
from .scripture import format_chinese_reference
from .storage import repository


EMAIL_PRODUCTION = is_truthy(os.getenv("RODUCTION"))
NOTIFICATION_PRODUCTION = is_truthy(os.getenv("PRODUCTION"))
TEST_RECIPIENT = os.getenv("SUNDAY_SERVICE_TEST_RECIPIENT", "junyang168@gmail.com")


def _parse_service_date(raw: str | None) -> Optional[date]:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _format_display_date(service_date: str) -> str:
    parsed = _parse_service_date(service_date)
    return parsed.isoformat() if parsed else service_date


def _load_services() -> list[SundayServiceEntry]:
    services = repository.list_sunday_services()
    if not services:
        raise RuntimeError(f"No Sunday services found in {SUNDAY_SERVICE_FILE}")
    return services


def _load_workers() -> list[SundayWorker]:
    workers = repository.list_sunday_workers()
    if not workers:
        raise RuntimeError(f"No Sunday workers found in {SUNDAY_WORKERS_FILE}")
    return workers


def _select_service(services: list[SundayServiceEntry], target_date: Optional[str]) -> SundayServiceEntry:
    if target_date:
        normalized = _normalize_date_text(target_date)
        for entry in services:
            parsed = _parse_service_date(entry.date)
            if parsed and parsed.isoformat() == normalized:
                return entry
        raise ValueError(f"Sunday service for date {target_date} not found")

    today = date.today()
    services_sorted = sorted(services, key=lambda entry: (_parse_service_date(entry.date) or date.max))
    for entry in services_sorted:
        parsed = _parse_service_date(entry.date)
        if parsed and parsed >= today:
            return entry
    return services_sorted[-1]


def _normalize_date_text(raw: str) -> str:
    parsed = _parse_service_date(raw)
    if parsed:
        return parsed.isoformat()
    return raw.strip()


def _resolve_sender() -> str:
    raw_sender = os.getenv("SUNDAY_SERVICE_SENDER") or os.getenv("SMTP_USERNAME")
    if not raw_sender:
        raise RuntimeError("SUNDAY_SERVICE_SENDER or SMTP_USERNAME must be set")
    display_name, email_address = parseaddr(raw_sender)
    if not email_address:
        email_address = raw_sender
    sender_name = display_name or os.getenv("SUNDAY_SERVICE_SENDER_NAME")
    return formataddr((sender_name, email_address)) if sender_name else email_address


def _collect_recipient_emails(workers: Iterable[SundayWorker]) -> list[str]:
    emails: "OrderedDict[str, None]" = OrderedDict()
    for worker in workers:
        if not worker.email:
            continue
        candidate = worker.email.strip()
        if not candidate:
            continue
        emails.setdefault(candidate)
    return list(emails)


def _fallback(value: Optional[str]) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else "未定"


def _format_scripture_reference(scripture: Iterable[str] | None) -> str:
    if not scripture:
        return "未定"
    values: list[str] = []
    for part in scripture:
        if not isinstance(part, str):
            continue
        trimmed = part.strip()
        if not trimmed:
            continue
        values.append(format_chinese_reference(trimmed))
    return "、".join(values) if values else "未定"


def _format_subject_date(raw_date: str) -> str:
    parsed = _parse_service_date(raw_date)
    if parsed:
        return parsed.strftime("%Y年%m月%d日")
    normalized = raw_date.strip() if isinstance(raw_date, str) else ""
    return normalized or raw_date or ""


def _build_congregation_subject(service: SundayServiceEntry) -> str:
    formatted = _format_subject_date(service.date)
    return f"聖道教會{formatted}主日崇拜"


@lru_cache()
def _load_congregation_template() -> str:
    template_path = resolve_data_base_dir() / "sunday_worship" / "sunday_service_reminder.html"
    try:
        return template_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Sunday service reminder template not found at {template_path}") from exc


def _build_congregation_email_html(service: SundayServiceEntry) -> str:
    template = _load_congregation_template()
    replacements = {
        "sermonTitle": _fallback(service.sermon_title),
        "sermonSpeaker": _fallback(service.sermon_speaker),
        "scriptureReference": _format_scripture_reference(service.scripture),
    }
    return template.format(**replacements)


def build_sunday_service_email_bodies(service: SundayServiceEntry) -> tuple[str, str]:
    sermon_speaker = _fallback(service.sermon_speaker)
    presider = _fallback(service.presider)
    worship_leader = _fallback(service.worship_leader)
    pianist = _fallback(service.pianist)
    readers = service.scripture_readers or []
    readers_text = "、".join(reader.strip() for reader in readers if reader.strip()) or "未定"

    text_body = (
        "本週服事同工\n"
        f"讲道 {sermon_speaker}\n"
        f"司 会 {presider}\n"
        f"领 诗 {worship_leader}\n"
        f"司  琴 {pianist}\n\n"
        "讀   經\n"
        f"{readers_text}\n"
    ).strip()

    html_body = f"""
<div style="font-family:Roboto,Helvetica,Arial,sans-serif;font-size:15px;color:#202124;line-height:1.6;">
  <p style="margin:0 0 12px 0;">本週服事同工</p>
  <table style="border-collapse:collapse;margin:0 0 16px 0;">
    <tbody>
      <tr><td style="padding:4px 24px 4px 0;">讲道</td><td style="padding:4px 0;">{sermon_speaker}</td></tr>
      <tr><td style="padding:4px 24px 4px 0;">司 会</td><td style="padding:4px 0;">{presider}</td></tr>
      <tr><td style="padding:4px 24px 4px 0;">领 诗</td><td style="padding:4px 0;">{worship_leader}</td></tr>
      <tr><td style="padding:4px 24px 4px 0;">司  琴</td><td style="padding:4px 0;">{pianist}</td></tr>
    </tbody>
  </table>
  <p style="margin:0 0 8px 0;">讀   經</p>
  <p style="margin:0;">{readers_text}</p>
</div>
""".strip()

    return text_body, html_body


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _resolve_ppt_path(service: SundayServiceEntry) -> Path:
    if not service.final_ppt_filename:
        raise RuntimeError(f"Service {service.date} does not have a finalized PPT")
    ppt_path = SUNDAY_WORSHIP_DIR / service.final_ppt_filename
    if not ppt_path.exists():
        raise FileNotFoundError(f"Finalized PPT not found at {ppt_path}")
    return ppt_path


def _send_congregation_email(
    smtp: smtplib.SMTP,
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    text_body: str,
    html_body: str,
) -> None:
    if not recipients:
        return

    batch_size = determine_recipient_batch_size()
    to_header = os.getenv("REMINDER_TO_HEADER", "Undisclosed recipients:;")
    cc_header = os.getenv("REMINDER_CC_HEADER")

    for batch in chunked(recipients, batch_size):
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to_header
        if cc_header:
            message["Cc"] = cc_header
        message["Subject"] = subject
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        smtp.send_message(message, to_addrs=batch)


def send_sunday_service_email(
    target_date: Optional[str],
    *,
    dry_run: bool = False,
) -> SundayServiceEmailResult:
    services = _load_services()
    workers = _load_workers()
    service = _select_service(services, target_date)
    worker_recipients = _collect_recipient_emails(workers)
    if not worker_recipients:
        raise RuntimeError("No worker email addresses available to send the Sunday service email.")
    congregation_recipients_path = determine_notification_recipients_file(NOTIFICATION_PRODUCTION)
    congregation_recipients = load_notification_recipients(congregation_recipients_path)

    ppt_path = _resolve_ppt_path(service)
    formatted_date = _format_display_date(service.date)
    subject = f"圣道教会{formatted_date}的主日崇拜同工安排及ppt"
    default_text_body, default_html_body = build_sunday_service_email_bodies(service)
    html_body = service.email_body_html or default_html_body
    text_body = (
        default_text_body if not service.email_body_html else _html_to_text(service.email_body_html)
    )
    congregation_html_body = _build_congregation_email_html(service)
    congregation_text_body = _html_to_text(congregation_html_body)
    congregation_subject = _build_congregation_subject(service)

    worker_recipient_list = list(worker_recipients)
    congregation_recipient_list = list(congregation_recipients)
    if not EMAIL_PRODUCTION:
        worker_recipient_list = [TEST_RECIPIENT]
        congregation_recipient_list = [TEST_RECIPIENT]

    if dry_run:
        worker_recipient_list = [TEST_RECIPIENT]
        congregation_recipient_list = [TEST_RECIPIENT]

    if not worker_recipient_list:
        raise RuntimeError("No worker email recipients resolved for Sunday service email.")

    host = os.getenv("SMTP_HOST")
    if not host:
        raise RuntimeError("SMTP_HOST environment variable is required")

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}
    sender = _resolve_sender()

    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(worker_recipient_list)
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    ppt_bytes = ppt_path.read_bytes()
    message.add_attachment(
        ppt_bytes,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=ppt_path.name,
    )

    with smtplib.SMTP(host, port) as smtp:
        smtp.ehlo()
        if use_tls:
            smtp.starttls()
            smtp.ehlo()
        if username and password:
            smtp.login(username, password)
        smtp.send_message(message, to_addrs=worker_recipient_list)
        _send_congregation_email(
            smtp,
            sender=sender,
            recipients=congregation_recipient_list,
            subject=congregation_subject,
            text_body=congregation_text_body,
            html_body=congregation_html_body,
        )

    return SundayServiceEmailResult(
        date=formatted_date,
        recipients=worker_recipient_list,
        ppt_filename=ppt_path.name,
        subject=subject,
        dry_run=False,
    )
