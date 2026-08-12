import json

from backend.api.sc_api.sermon_comment import SermonCommentManager


def test_sermon_bookmark_round_trip_and_update(tmp_path) -> None:
    """Exercise the real manager without mutating app data or global modules."""
    manager = SermonCommentManager()
    manager.bookmark_file = str(tmp_path / "bookmark" / "bookmark.json")
    user_id = "test_user@example.com"
    item = "test_sermon_123"

    manager.set_bookmark(user_id, item, "[10_20]")
    assert manager.get_bookmark(user_id, item) == {"index": "[10_20]"}

    with open(manager.bookmark_file, encoding="utf-8") as handle:
        stored = json.load(handle)
    assert stored[manager.get_key(user_id, item)]["index"] == "[10_20]"

    manager.set_bookmark(user_id, item, "[30_40]")
    assert manager.get_bookmark(user_id, item) == {"index": "[30_40]"}
