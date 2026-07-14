from backend.api import service
from backend.api.models import FellowshipEntry


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
