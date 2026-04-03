import unittest

from backend.api.models import SundayWorker


class SundayWorkerTests(unittest.TestCase):
    def test_preferred_roles_normalize_and_dedupe(self):
        worker = SundayWorker.model_validate(
            {
                "name": "同工甲",
                "preferredRoles": ["領詩", "司會", "領詩", " 司琴 "],
            }
        )

        self.assertEqual(worker.preferred_roles, ["領詩", "司會", "司琴"])
        self.assertEqual(
            worker.model_dump(by_alias=True, exclude_none=True)["preferredRoles"],
            ["領詩", "司會", "司琴"],
        )

    def test_preferred_roles_accept_legacy_field_name(self):
        worker = SundayWorker.model_validate(
            {
                "name": "同工乙",
                "preferred_role": "司琴",
            }
        )

        self.assertEqual(worker.preferred_roles, ["司琴"])


if __name__ == "__main__":
    unittest.main()
