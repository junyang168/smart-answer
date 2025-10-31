from __future__ import annotations

import os
import smtplib
from collections import OrderedDict
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Iterable, Optional

from .config import SUNDAY_SERVICE_FILE, SUNDAY_WORKERS_FILE, SUNDAY_WORSHIP_DIR
from .models import (
    SundayServiceEmailResult,
    SundayServiceEntry,
    SundayWorker,
)
from .storage import repository


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


def _build_email_bodies(service: SundayServiceEntry) -> tuple[str, str]:
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


def _resolve_ppt_path(service: SundayServiceEntry) -> Path:
    if not service.final_ppt_filename:
        raise RuntimeError(f"Service {service.date} does not have a finalized PPT")
    ppt_path = SUNDAY_WORSHIP_DIR / service.final_ppt_filename
    if not ppt_path.exists():
        raise FileNotFoundError(f"Finalized PPT not found at {ppt_path}")
    return ppt_path


def send_sunday_service_email(
    target_date: Optional[str],
    *,
    dry_run: bool = False,
) -> SundayServiceEmailResult:
    services = _load_services()
    workers = _load_workers()
    service = _select_service(services, target_date)
    recipients = _collect_recipient_emails(workers)
    if not recipients:
        raise RuntimeError("No worker email addresses available to send the Sunday service email.")

    ppt_path = _resolve_ppt_path(service)
    formatted_date = _format_display_date(service.date)
    subject = f"圣道教会{formatted_date}的主日崇拜同工安排及ppt"
    text_body, html_body = _build_email_bodies(service)

    if dry_run:
        recipients = ['junyang168@gmail.com']

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
    message["To"] = ", ".join(recipients)
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
        smtp.send_message(message, to_addrs=recipients)

    return SundayServiceEmailResult(
        date=formatted_date,
        recipients=recipients,
        ppt_filename=ppt_path.name,
        subject=subject,
        dry_run=False,
    )
