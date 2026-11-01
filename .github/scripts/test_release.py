import unittest
from datetime import datetime, timezone

from release import (
    bump_version,
    build_release_body,
    added_readme_links,
    release_is_due,
    release_type_for,
)


class ReleaseTests(unittest.TestCase):
    def test_bump_version_for_each_release_type(self):
        self.assertEqual(bump_version("v1.1.0", "Patch"), "v1.1.1")
        self.assertEqual(bump_version("v1.1.0", "Minor"), "v1.2.0")
        self.assertEqual(bump_version("v1.1.0", "Major"), "v2.0.0")

    def test_rejects_non_semantic_release_tag(self):
        with self.assertRaises(ValueError):
            bump_version("latest", "Patch")

    def test_release_type_uses_highest_merged_pr_label(self):
        pull_requests = [
            {"labels": [{"name": "release:minor"}]},
            {"labels": [{"name": "release:major"}]},
        ]
        self.assertEqual(release_type_for(pull_requests), "Major")
        self.assertEqual(release_type_for([{"labels": []}]), "Patch")

    def test_release_interval_counts_calendar_days(self):
        published_at = datetime(2026, 10, 28, 23, 59, tzinfo=timezone.utc)
        now = datetime(2026, 11, 2, 0, 1, tzinfo=timezone.utc)
        self.assertTrue(release_is_due(published_at, now))

        four_days_ago = datetime(2026, 10, 29, 0, 1, tzinfo=timezone.utc)
        self.assertFalse(release_is_due(four_days_ago, now))

    def test_added_readme_links_only_includes_new_resource_urls(self):
        previous = "- [Existing](https://example.com) — Existing tool.\n"
        current = (
            previous
            + "- [New Tool](https://new.example.com) — A new resource.\n"
            + "- [Changed name](https://example.com) — Updated description.\n"
        )
        self.assertEqual(
            added_readme_links(previous, current),
            ["- [New Tool](https://new.example.com) — A new resource."],
        )

    def test_body_lists_changes_from_each_source(self):
        repository = {
            "name": "awesome-links",
            "html_url": "https://github.com/nikomarinovic/awesome-links",
        }
        pull_requests = [
            {
                "title": "Add a useful resource",
                "number": 15,
                "html_url": "https://github.com/nikomarinovic/awesome-links/pull/15",
                "body": "## Summary\nAdds a useful resource and validation tests.\n\n## Tests\nPassed.",
            },
        ]
        issues = [
            {
                "title": "Suggest a useful resource",
                "number": 14,
                "html_url": "https://github.com/nikomarinovic/awesome-links/issues/14",
                "body": "Please add this useful resource.",
            },
        ]
        commits = [
            {
                "sha": "abcdef123456",
                "html_url": "https://github.com/nikomarinovic/awesome-links/commit/abcdef",
                "commit": {"message": "Improve resource validation\n\nDetails"},
            },
        ]
        body = build_release_body(
            repository,
            "nikomarinovic",
            "Niko Marinović",
            "v1.1.1",
            "v1.1.0",
            "Patch",
            "02-11-2026",
            pull_requests,
            issues,
            commits,
            ["- [New Tool](https://new.example.com) — A useful tool."],
        )
        for expected in (
            "# awesome-links v1.1.1",
            "**Release Type:** `Patch`",
            "**Author:** [@nikomarinovic]",
            "**Date:** 02-11-2026",
            "## Summary",
            "1 new README link",
            "## New links added to README",
            "[#15](https://github.com/nikomarinovic/awesome-links/pull/15)",
            "Adds a useful resource and validation tests.",
            "## Merged pull requests",
            "[#14](https://github.com/nikomarinovic/awesome-links/issues/14)",
            "Please add this useful resource.",
            "## Closed issues",
            "[abcdef1](https://github.com/nikomarinovic/awesome-links/commit/abcdef)",
            "## Standalone commits",
            "**Full Changelog**:",
            "© 2026 Niko Marinović. All rights reserved.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, body)


if __name__ == "__main__":
    unittest.main()
