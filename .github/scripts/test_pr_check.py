import unittest

from pr_check import PageTextParser, analyze, content_findings


class ContentPolicyTests(unittest.TestCase):
    def test_detects_restricted_terms_with_unicode_obfuscation(self):
        self.assertIn("adult or sexually explicit content", content_findings("Free P\u200born videos"))

    def test_detects_encoded_restricted_text(self):
        self.assertIn("gambling", content_findings("&#99;asino.example"))

    def test_detects_each_restricted_category(self):
        samples = {
            "adult or sexually explicit content": "NSFW adult content",
            "gambling": "Online casino and sports betting",
            "graphic violence": "Graphic violence and torture footage",
            "illegal drug sales or promotion": "Buy cocaine from our drug marketplace",
            "hate or extremist content": "White supremacy and neo-Nazi propaganda",
        }
        for expected, text in samples.items():
            with self.subTest(category=expected):
                self.assertIn(expected, content_findings(text))

    def test_educational_mention_of_category_is_not_always_blocked(self):
        self.assertEqual(content_findings("A guide to preventing online scams"), [])

    def test_page_parser_ignores_scripts_and_reads_description_metadata(self):
        parser = PageTextParser()
        parser.feed(
            '<html><head><meta name="description" content="Useful software">'
            '<script>online casino</script></head><body>Developer tools</body></html>'
        )
        self.assertEqual(" ".join(parser.parts), "Useful software Developer tools")

    def test_analysis_rejects_restricted_page_content(self):
        diff = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1,2 +1,3 @@\n"
            " - [Existing](https://example.org) — Existing useful link.\n"
            "+- [Casino](https://casino.example) — Online casino and sports betting.\n"
            " \n"
        )
        ok, lines = analyze(
            diff,
            [("README.md", "modified")],
            "- [Existing](https://example.org) — Existing useful link.\n",
            reach_fn=lambda _url: (True, "HTTP 200", "Online casino and sports betting"),
            resolve_fn=lambda _host: None,
        )
        self.assertFalse(ok)
        self.assertTrue(any("gambling" in line for line in lines))

    def test_analysis_rejects_embedded_description_links(self):
        diff = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1,2 +1,3 @@\n"
            " - [Existing](https://example.org) — Existing useful link.\n"
            "+- [New](https://new.example) — Click [this](https://other.example) for details.\n"
            " \n"
        )
        ok, lines = analyze(
            diff,
            [("README.md", "modified")],
            "- [Existing](https://example.org) — Existing useful link.\n",
            reach_fn=lambda _url: (True, "HTTP 200", "Useful page text"),
            resolve_fn=lambda _host: None,
        )
        self.assertFalse(ok)
        self.assertTrue(any("do not embed extra links" in line for line in lines))

    def test_analysis_rejects_shortener_subdomains(self):
        diff = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1,2 +1,3 @@\n"
            " - [Existing](https://example.org) — Existing useful link.\n"
            "+- [New](https://go.bit.ly/abc) — A useful new resource.\n"
            " \n"
        )
        ok, lines = analyze(
            diff,
            [("README.md", "modified")],
            "- [Existing](https://example.org) — Existing useful link.\n",
            reach_fn=lambda _url: (True, "HTTP 200", "Useful page text"),
            resolve_fn=lambda _host: self.fail("shortener should be rejected before DNS lookup"),
        )
        self.assertFalse(ok)
        self.assertTrue(any("URL shorteners are not allowed" in line for line in lines))

    def test_analysis_requires_manual_review_when_page_cannot_be_scanned(self):
        diff = (
            "diff --git a/README.md b/README.md\n"
            "--- a/README.md\n"
            "+++ b/README.md\n"
            "@@ -1,2 +1,3 @@\n"
            " - [Existing](https://example.org) — Existing useful link.\n"
            "+- [New](https://new.example) — A useful new resource.\n"
            " \n"
        )
        ok, lines = analyze(
            diff,
            [("README.md", "modified")],
            "- [Existing](https://example.org) — Existing useful link.\n",
            reach_fn=lambda _url: (True, "HTTP 403; site blocks automated review", None),
            resolve_fn=lambda _host: None,
        )
        self.assertFalse(ok)
        self.assertTrue(any("maintainer review is required" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
