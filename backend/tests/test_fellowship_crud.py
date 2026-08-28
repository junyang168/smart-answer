from backend.api import service
from backend.api.models import FellowshipEmailContent, FellowshipEntry


def test_update_fellowship_accepts_iso_date(monkeypatch):
    entry = FellowshipEntry(date="06/05/2026", title="Updated title")
    received = {}

    def update_fellowship(date, payload):
        received["date"] = date
        received["payload"] = payload
        return payload

    monkeypatch.setattr(service.repository, "update_fellowship", update_fellowship)

    assert service.update_fellowship("2026-06-05", entry) == entry
    assert received == {"date": "06/05/2026", "payload": entry}


def test_delete_fellowship_accepts_iso_date(monkeypatch):
    received = {}

    def delete_fellowship(date):
        received["date"] = date

    monkeypatch.setattr(service.repository, "delete_fellowship", delete_fellowship)

    service.delete_fellowship("2026-06-05")

    assert received["date"] == "06/05/2026"


def test_get_fellowship_email_content_accepts_iso_date(monkeypatch):
    entry = FellowshipEntry(
        date="06/05/2026",
        emailSubject="Saved subject",
        emailBodyHtml="<p>Saved body</p>",
    )
    monkeypatch.setattr(service, "list_fellowships", lambda: [entry])

    content = service.get_fellowship_email_content("2026-06-05")

    assert content == FellowshipEmailContent(subject="Saved subject", html="<p>Saved body</p>")


def test_update_fellowship_email_content_accepts_iso_date(monkeypatch):
    payload = FellowshipEmailContent(subject="Subject", html="<p>Body</p>")
    received = {}

    def set_fellowship_email_content(date, subject, html):
        received.update(date=date, subject=subject, html=html)
        return FellowshipEntry(date=date, emailSubject=subject, emailBodyHtml=html)

    monkeypatch.setattr(
        service.repository,
        "set_fellowship_email_content",
        set_fellowship_email_content,
    )

    assert service.update_fellowship_email_content("2026-06-05", payload) == payload
    assert received == {
        "date": "06/05/2026",
        "subject": "Subject",
        "html": "<p>Body</p>",
    }


def test_get_fellowship_email_content_uses_reminder_template(monkeypatch):
    monkeypatch.delenv("REMINDER_SUBJECT", raising=False)
    monkeypatch.delenv("REMINDER_BODY_TEMPLATE", raising=False)
    monkeypatch.delenv("REMINDER_BODY_TEMPLATE_HTML", raising=False)
    entry = FellowshipEntry(
        date="06/05/2026",
        host="Host",
        title="Title",
        series="Series",
        sequence=3,
    )
    monkeypatch.setattr(service, "list_fellowships", lambda: [entry])

    content = service.get_fellowship_email_content("2026-06-05")

    assert content.subject == "達拉斯圣道教会团契 时间: 06/05 周五晚 7:30 - 9:00 CST "
    assert "弟兄姊妹们平安" in content.html
    assert "主持人:</td>" in content.html
    assert "Series 系列 的第 3 講" in content.html
    assert "Google 線上會議" in content.html
    assert "觀看過往團契分享" in content.html
    assert 'href="https://dallas-hlc.org/resources/fellowship"' in content.html


def test_get_fellowship_email_content_honors_reminder_template_override(monkeypatch):
    monkeypatch.setenv("REMINDER_SUBJECT", "Template subject {date}")
    monkeypatch.setenv(
        "REMINDER_BODY_TEMPLATE_HTML",
        "<p>{date}|{host}|{title}|{series}|{sequence}</p>",
    )
    entry = FellowshipEntry(
        date="06/05/2026",
        host="Host",
        title="Title",
        series="Series",
        sequence=3,
    )
    monkeypatch.setattr(service, "list_fellowships", lambda: [entry])

    content = service.get_fellowship_email_content("2026-06-05")

    assert content.subject == "Template subject {date}"
    assert content.html == "<p>06/05|Host|Title|Series|3</p>"
