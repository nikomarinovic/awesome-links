import os
import tempfile
import unittest
from unittest.mock import ANY, patch

from issue_bot import (
    add_resources,
    infer_category,
    main,
    parse_resources,
    short_description,
    validate_resources,
)


class IssueBotTests(unittest.TestCase):
    def test_parses_existing_link_suggestion_issue(self):
        issue = {
            "body": (
                "### Resource Name\n\nArtiGram\n\n"
                "### Resource URL\n\nhttps://artigram.cafeboon.com\n\n"
                "### Category\n\nAI\n\n"
                "### Why should it be included?\n\n"
                "I built ArtiGram, a free gallery where people and AI assistants "
                "post interactive HTML artifacts such as calculators, data "
                "visualisations, games and lessons, each as a live page. "
                "It also has a remote MCP server."
            )
        }

        with patch(
            "issue_bot.page_description",
            side_effect=AssertionError("provided description should be used"),
        ):
            resources = parse_resources(issue)

        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["name"], "ArtiGram")
        self.assertEqual(resources[0]["url"], "https://artigram.cafeboon.com")
        self.assertEqual(resources[0]["category"], "AI")
        self.assertEqual(resources[0]["section"], "ai")
        self.assertTrue(resources[0]["description"].startswith("A free gallery"))

    def test_parses_multiple_names_and_urls_without_optional_fields(self):
        issue = {
            "body": (
                "### Resource Name\n\nColor Studio\nFont Finder\n\n"
                "### Resource URL\n\nhttps://colors.example\nhttps://fonts.example\n\n"
                "### Category\n\n\n"
                "### Why should it be included?\n\n"
            )
        }

        with patch(
            "issue_bot.page_description",
            side_effect=["Create custom color palettes.", "Browse typefaces."],
        ):
            resources = parse_resources(issue)

        self.assertEqual(
            [(item["category"], item["section"]) for item in resources],
            [("Colors", "colors"), ("Fonts & Typography", "fonts--typography")],
        )
        self.assertEqual(
            [item["description"] for item in resources],
            ["Create custom color palettes.", "Browse typefaces."],
        )

    def test_infers_category_when_missing_or_invalid(self):
        self.assertEqual(
            infer_category("ArtiGram", "https://example.com", "AI assistant MCP gallery"),
            "AI",
        )
        self.assertEqual(
            infer_category("Unknown", "https://example.com", ""),
            "Miscellaneous",
        )

    def test_generates_a_concise_plain_description(self):
        self.assertEqual(
            short_description(
                "I built ArtiGram, a free gallery where people post interactive "
                "HTML artifacts. It also has a server.",
                "ArtiGram",
            ),
            "A free gallery where people post interactive HTML artifacts.",
        )

    def test_adds_multiple_resources_to_the_matching_sections(self):
        readme = (
            '<div id="ai">\n\n'
            "- [Existing](https://existing.example) — Existing useful tool.\n\n"
            '<h3 id="colors">Colors</h3>\n\n'
            "- [Palette](https://palette.example) — Existing color tool.\n"
        )
        resources = [
            {
                "name": "New AI",
                "url": "https://new-ai.example",
                "category": "AI",
                "section": "ai",
                "description": "A useful AI resource.",
            },
            {
                "name": "New Colors",
                "url": "https://new-colors.example",
                "category": "Colors",
                "section": "colors",
                "description": "A useful color palette.",
            },
        ]

        updated, additions = add_resources(readme, resources)

        self.assertEqual(len(additions), 2)
        self.assertIn(
            "- [New AI](https://new-ai.example) — A useful AI resource.",
            updated,
        )
        self.assertIn(
            "- [New Colors](https://new-colors.example) — A useful color palette.",
            updated,
        )
        self.assertLess(updated.index("New AI"), updated.index("Colors"))

    def test_duplicate_urls_are_not_added_twice(self):
        readme = (
            '<div id="ai">\n\n'
            "- [Existing](https://example.com/) — Existing useful tool.\n"
        )
        resource = {
            "name": "Duplicate",
            "url": "https://example.com",
            "category": "AI",
            "section": "ai",
            "description": "A useful AI resource.",
        }

        updated, additions = add_resources(readme, [resource])

        self.assertEqual(updated, readme)
        self.assertEqual(additions, [])

    def test_uses_existing_link_policy_before_adding_resources(self):
        readme = (
            '<div id="ai">\n\n'
            "- [Existing](https://existing.example) — Existing useful tool.\n"
        )
        updated, _ = add_resources(
            readme,
            [
                {
                    "name": "New AI",
                    "url": "https://new-ai.example",
                    "category": "AI",
                    "section": "ai",
                    "description": "A useful AI resource.",
                }
            ],
        )

        valid, findings = validate_resources(
            readme,
            updated,
            reach_fn=lambda _url: (True, "HTTP 200", "Useful AI resource"),
            resolve_fn=lambda _host: None,
        )

        self.assertTrue(valid, findings)

    def test_rejects_restricted_content_before_readme_changes(self):
        readme = (
            '<div id="ai">\n\n'
            "- [Existing](https://existing.example) — Existing useful tool.\n"
        )
        updated, _ = add_resources(
            readme,
            [
                {
                    "name": "Casino AI",
                    "url": "https://new-ai.example",
                    "category": "AI",
                    "section": "ai",
                    "description": "An AI casino and sports betting platform.",
                }
            ],
        )

        valid, findings = validate_resources(
            readme,
            updated,
            reach_fn=lambda _url: (True, "HTTP 200", "AI casino and betting"),
            resolve_fn=lambda _host: None,
        )

        self.assertFalse(valid)
        self.assertTrue(any("gambling" in finding for finding in findings))

    def test_rejects_non_https_urls(self):
        issue = {
            "body": (
                "### Resource Name\n\nExample\n\n"
                "### Resource URL\n\nhttp://example.com\n\n"
                "### Category\n\nAI\n"
            )
        }
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            parse_resources(issue)

    def test_non_link_issues_are_assigned_and_left_open(self):
        issue = {
            "number": 22,
            "title": "Report a broken link",
            "body": "The apps page is unavailable.",
            "state": "open",
        }
        with (
            patch.dict(
                os.environ,
                {"REPO": "owner/repo", "ISSUE_NUMBER": "22", "OWNER_LOGIN": "owner"},
            ),
            patch("issue_bot.gh_api", return_value=issue),
            patch("issue_bot.ensure_issue_assigned") as assign,
            patch("issue_bot.comment") as add_comment,
            patch("issue_bot.gh_label_name") as add_label,
            patch("issue_bot.close_issue") as close,
        ):
            main()

        assign.assert_called_once_with(issue, "owner")
        add_comment.assert_called_once()
        self.assertIn("only process link suggestions", add_comment.call_args.args[1])
        add_label.assert_not_called()
        close.assert_not_called()

    def test_link_issues_update_readme_and_close_after_validation(self):
        issue = {
            "number": 23,
            "title": "Please add this useful resource",
            "body": (
                "### Resource Name\n\nExample\n\n"
                "### Resource URL\n\nhttps://example.com\n"
            ),
            "state": "open",
        }
        resource = {
            "name": "Example",
            "url": "https://example.com",
            "category": "AI",
            "section": "ai",
            "description": "A useful example resource.",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_cwd = os.getcwd()
            try:
                os.chdir(temp_dir)
                with open("README.md", "w", encoding="utf-8") as file:
                    file.write("base README")
                with (
                    patch.dict(
                        os.environ,
                        {"REPO": "owner/repo", "ISSUE_NUMBER": "23", "OWNER_LOGIN": "owner"},
                    ),
                    patch(
                        "issue_bot.gh_api",
                        side_effect=[issue, []],
                    ),
                    patch("issue_bot.ensure_issue_assigned"),
                    patch("issue_bot.gh_label_name"),
                    patch("issue_bot.add_issue_label") as label,
                    patch("issue_bot.parse_resources", return_value=[resource]),
                    patch(
                        "issue_bot.add_resources",
                        return_value=("updated README", [resource]),
                    ),
                    patch("issue_bot.validate_resources", return_value=(True, [])),
                    patch("issue_bot.commit_readme", return_value=True) as commit,
                    patch("issue_bot.comment") as add_comment,
                    patch("issue_bot.close_issue") as close,
                ):
                    main()
            finally:
                os.chdir(previous_cwd)

        label.assert_called_once_with(23, "new resource")
        commit.assert_called_once_with("23", ANY, "updated README")
        add_comment.assert_called_once()
        self.assertIn("README was updated by this issue", add_comment.call_args.args[1])
        close.assert_called_once_with("23")


if __name__ == "__main__":
    unittest.main()
