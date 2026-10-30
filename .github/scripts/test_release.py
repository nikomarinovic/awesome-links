import unittest

from release import bump_version, build_release_body, release_type_for


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

    def test_body_contains_requested_release_sections_and_metadata(self):
        repository = {
            "name": "awesome-links",
            "html_url": "https://github.com/nikomarinovic/awesome-links",
        }
        pull_requests = [
            {
                "title": "Add a useful resource",
                "number": 15,
                "html_url": "https://github.com/nikomarinovic/awesome-links/pull/15",
            }
        ]
        body = build_release_body(
            repository,
            "nikomarinovic",
            "Niko Marinović",
            "v1.1.1",
            "v1.1.0",
            "Patch",
            "2026-11-02",
            pull_requests,
            "## What's Changed\n* Add a useful resource by @contributor",
        )
        for expected in (
            "# awesome-links v1.1.1",
            "**Release Type:** `Patch`",
            "**Author:** [@nikomarinovic]",
            "**Date:** 2026-11-02",
            "## Summary",
            "## Highlights",
            "## What's Next",
            "## What's Changed",
            "**Full Changelog**:",
            "© 2026 Niko Marinović. All rights reserved.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, body)
        self.assertEqual(body.count("## What's Changed"), 1)


if __name__ == "__main__":
    unittest.main()
