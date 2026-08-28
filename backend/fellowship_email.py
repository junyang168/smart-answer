from __future__ import annotations

from datetime import date, datetime, time
import html
import os
import re

from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/Chicago")


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


def build_fellowship_email_content(
    *,
    event_date: date,
    host: str | None = None,
    title: str | None = None,
    series: str | None = None,
    sequence: int | None = None,
    custom_subject: str | None = None,
    custom_html: str | None = None,
) -> tuple[str, str, str]:
    """Render the same fellowship notification content for jobs and admin sends."""
    formatted_date = datetime.combine(event_date, time.min, tzinfo=TIMEZONE).strftime("%m/%d")
    subject = (custom_subject or "").strip() or os.getenv(
        "REMINDER_SUBJECT",
        f"圣道教会 {formatted_date} 周五团契 时间改為周五晚 7:30 - 9:00 CST ",
    )
    rendered_custom_html = (custom_html or "").strip()
    if rendered_custom_html:
        return subject, _html_to_text(rendered_custom_html), rendered_custom_html

    details_lines: list[str] = []
    if host:
        details_lines.append(f"主持人: {host} ")
    if series:
        series_line = f"系列: {series} 系列"
        if sequence is not None:
            series_line += f" 的第 {sequence} 講"
        details_lines.append(series_line)
    if title:
        details_lines.append(f"主題: {title}")

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
        host=host or "",
        title=title or "",
        series=series or "",
        sequence=sequence or "",
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
                f'<td style="padding:4px 12px 4px 0;">{label}:</td>'
                f'<td style="padding:4px 0;font-weight:600;">{value}</td>'
                "</tr>"
            )
        details_html = (
            '<table style="border-collapse:collapse;margin:16px 0;">'
            + "".join(rows)
            + "</table>"
        )

    default_html = (
        '<div style="font-family:Roboto,Helvetica,Arial,sans-serif;font-size:16px;color:#202124;">'
        '  <p style="margin:0 0 16px 0;">弟兄姊妹们平安，</p>'
        '  <p style="margin:0 0 16px 0;">'
        "    圣道教会每两周一次的团契自本週 <strong> {date} </strong>起將改為線上進行。时间改為周五晚 7:30 - 9:00 CST。欢迎大家参加。<br/>"
        "  </p>"
        "  {details_html}"
        "</div>"
        "<div>"
        '    <a href="https://meet.google.com/mhc-nafs-ahn">Google 線上會議</a>'
        "</div>"
        "<div><br/><br/>"
        '<a href="https://www.dallas-hlc.org/resources/articles">觀看過往團契分享</a>'
        "</div>"
    )
    html_body_template = os.getenv("REMINDER_BODY_TEMPLATE_HTML", default_html)
    html_body = html_body_template.format(
        date=formatted_date,
        details_html=details_html,
        host=host or "",
        title=title or "",
        series=series or "",
        sequence=sequence or "",
    )

    return subject, text_body, html_body
