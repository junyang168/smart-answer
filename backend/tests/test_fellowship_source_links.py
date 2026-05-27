from backend.api.models import FellowshipEntry


def test_fellowship_entry_source_links_round_trip_with_aliases():
    entry = FellowshipEntry.model_validate(
        {
            "date": "05/22/2026",
            "host": "楊軍 弟兄",
            "title": "天國的鑰匙",
            "sourceLinks": [
                {
                    "label": "王守仁牧師講道",
                    "url": "https://example.com/sermon",
                }
            ],
            "summary": "**本次查經**回顧",
            "keyLearnings": ["**基督**是教會的根基"],
        }
    )

    dumped = entry.model_dump(by_alias=True)

    assert dumped["sourceLinks"] == [
        {
            "label": "王守仁牧師講道",
            "url": "https://example.com/sermon",
        }
    ]
    assert dumped["summary"] == "**本次查經**回顧"
    assert dumped["keyLearnings"] == ["**基督**是教會的根基"]
