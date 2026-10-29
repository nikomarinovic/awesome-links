#!/usr/bin/env python3
"""Validate a pull request that adds links to README.md.

Exit code 0 = every check passed (safe to merge automatically), 1 = something failed.
The pull request's code is never checked out or executed: only its diff and file
list are read through the GitHub API, so untrusted PRs cannot run anything here.
A human-readable report is written to report.md.
"""

import html
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

MAX_NEW_LINKS = 10
MAX_PAGE_BYTES = 512 * 1024
UA = "Mozilla/5.0 (compatible; awesome-links-checker/1.0)"

# - [Name](https://example.com) — short description.
ENTRY = re.compile(
    r"^- \[(?P<name>[^\]\n]{1,60})\]\((?P<url>https://[^)\s]+)\) [—–-] (?P<desc>.{5,300})$"
)
EMBEDDED_LINK = re.compile(r"(?:https?://|www\.|!\[|\[[^\]]+\]\s*\(|<[^>]+>)", re.I)
SHORTENERS = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly"}
CONTENT_RULES = {
    "adult or sexually explicit content": re.compile(
        r"\b(?:porn(?:ography)?|hentai|xxx|nsfw|onlyfans|escorts?|erotic(?:s)?|"
        r"adult\s+(?:content|site|entertainment)|sex(?:ual)?\s+"
        r"(?:content|chat|cam|video|toys?|work|services?)|cam\s*(?:girls?|shows?)|fetish)\b",
        re.I,
    ),
    "gambling": re.compile(
        r"\b(?:gambling|casinos?|sportsbooks?|sports\s+betting|online\s+betting|"
        r"betting\s+(?:site|platform)|slot\s+machines?|real[- ]money\s+poker|"
        r"kockanje|kladionice?|klađenje|rulet)\b",
        re.I,
    ),
    "graphic violence": re.compile(
        r"\b(?:gore|snuff|beheadings?|execution\s+(?:videos?|footage)|"
        r"graphic\s+violence|torture\s+(?:videos?|footage)|"
        r"real\s+(?:death|murder)\s+(?:videos?|footage)|extreme\s+violence|"
        r"mučenje|odrubljivanje)\b",
        re.I,
    ),
    "illegal drug sales or promotion": re.compile(
        r"\b(?:(?:buy|sell|order|shop(?:ping)?\s+for)\s+"
        r"(?:cocaine|heroin|meth(?:amphetamine)?|fentanyl|mdma|ecstasy)|"
        r"drug\s+(?:marketplace|market|shop)|darknet\s+market|"
        r"(?:prodaja|kupi|kupnja)\s+(?:kokaina|heroina|metamfetamina|fentanila))\b",
        re.I,
    ),
    "hate or extremist content": re.compile(
        r"\b(?:white\s+supremac(?:y|ist)|neo[- ]nazis?|racial\s+superiority|"
        r"ethnostate|antisemitic\s+(?:propaganda|content)|"
        r"promote\s+(?:hate|racial\s+violence)|"
        r"nadmoć\s+bjele\s+rase|neonacisti)\b",
        re.I,
    ),
}


def safe(text, limit=120):
    """Make user-supplied text harmless inside a Markdown comment."""
    return text.replace("`", "'").replace("@", "@​")[:limit]


def norm_host(host):
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def is_shortener(host):
    normalized = norm_host(host)
    return any(normalized == domain or normalized.endswith("." + domain) for domain in SHORTENERS)


def norm(url):
    p = urlparse(url)
    return norm_host(p.hostname or "") + p.path.rstrip("/")


def check_host(host):
    """Return an error string if the domain is unusable, else None."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return "domain does not resolve"
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            return "domain points to a non-public address"
    return None


class PageTextParser(HTMLParser):
    """Extract visible page text and descriptive metadata without scripts or styles."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.ignored += 1
        if tag == "meta" and attrs.get("name", "").lower() in {
            "description", "og:title", "og:description",
        }:
            self.parts.append(attrs.get("content", ""))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "template", "svg"} and self.ignored:
            self.ignored -= 1

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


class PublicHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlparse(newurl)
        if (target.scheme != "https" or not target.hostname or target.username
                or target.password or target.port not in (None, 443)):
            raise urllib.error.HTTPError(newurl, code, "unsafe redirect", headers, fp)
        host_error = check_host(target.hostname)
        if host_error:
            raise urllib.error.HTTPError(newurl, code, host_error, headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def reach(url):
    """Return (ok, detail, page_text); inaccessible pages require manual review."""
    last = "no response"
    opener = urllib.request.build_opener(PublicHttpsRedirectHandler)
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
            with opener.open(req, timeout=20) as resp:
                if method == "HEAD":
                    continue
                content_type = resp.headers.get_content_type()
                if content_type not in {"text/html", "text/plain", "application/xhtml+xml"}:
                    return True, f"HTTP {resp.status}; page content is not readable text", None
                raw = resp.read(MAX_PAGE_BYTES + 1)
                if len(raw) > MAX_PAGE_BYTES:
                    return True, f"HTTP {resp.status}; page is too large to scan", None
                charset = resp.headers.get_content_charset() or "utf-8"
                try:
                    page = raw.decode(charset, errors="replace")
                except LookupError:
                    page = raw.decode("utf-8", errors="replace")
                parser = PageTextParser()
                parser.feed(page)
                page_text = " ".join(parser.parts).strip()
                if not page_text:
                    return True, f"HTTP {resp.status}; no readable page text was found", None
                return True, f"HTTP {resp.status}", page_text
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 429):
                return True, f"HTTP {exc.code}; site blocks automated review", None
            last = f"HTTP {exc.code}"
        except Exception as exc:
            last = type(exc).__name__
    return False, last, None


def policy_text(text):
    """Normalize common Unicode obfuscation before matching content-policy terms."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(
        " " if unicodedata.category(char) == "Cc"
        else "" if unicodedata.category(char) == "Cf"
        else char
        for char in normalized
    )


def content_findings(text):
    normalized = policy_text(unquote(html.unescape(text)))
    return [category for category, pattern in CONTENT_RULES.items() if pattern.search(normalized)]


def parse_diff(diff):
    """Return {filename: [hunk, ...]} where a hunk is a list of (kind, text); kind is '+', '-' or ' '."""
    files = {}
    cur = None
    hunk = None
    for line in diff.rstrip("\n").split("\n"):
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+) b/(.+)$", line)
            cur = files.setdefault(m.group(2) if m else line, [])
            hunk = None
            continue
        if cur is None:
            continue
        if line.startswith("@@"):
            hunk = []
            cur.append(hunk)
            continue
        if hunk is None or line.startswith("\\"):
            continue
        if line.startswith("+"):
            hunk.append(("+", line[1:]))
        elif line.startswith("-"):
            hunk.append(("-", line[1:]))
        else:
            hunk.append((" ", line[1:] if line.startswith(" ") else line))
    return files


def added_runs(hunk):
    """Yield (start, end) index pairs of consecutive added lines."""
    i = 0
    while i < len(hunk):
        if hunk[i][0] == "+":
            j = i
            while j + 1 < len(hunk) and hunk[j + 1][0] == "+":
                j += 1
            yield i, j
            i = j + 1
        else:
            i += 1


def analyze(diff, changed, base_readme, reach_fn=reach, resolve_fn=check_host):
    """Return (ok, report_lines)."""
    lines = []
    state = {"ok": True}

    def fail(msg):
        state["ok"] = False
        lines.append(f"- ❌ {msg}")

    def good(msg):
        lines.append(f"- ✅ {msg}")

    # 1. only README.md may change
    bad = [f"`{safe(n)}`" for n, status in changed if n != "README.md" or status != "modified"]
    if bad or not changed:
        fail("This pull request may only change `README.md`. "
             + ("Found: " + ", ".join(bad) if bad else "No changes found."))
        return False, lines
    good("Only `README.md` was changed")

    # 2. only additions
    hunks = parse_diff(diff).get("README.md", [])
    removed = sum(1 for h in hunks for kind, _ in h if kind == "-")
    if removed:
        fail(f"{removed} existing line(s) were removed or edited. Keep existing content unchanged and add new link lines only.")
        return False, lines

    # 3. collect added lines, remembering whether they sit next to an existing list item
    entries = []
    for hunk in hunks:
        for i, j in added_runs(hunk):
            neighbours = [hunk[i - 1] if i > 0 else None, hunk[j + 1] if j + 1 < len(hunk) else None]
            placed = any(n and n[0] == " " and n[1].startswith("- ") for n in neighbours)
            for k in range(i, j + 1):
                text = hunk[k][1]
                if text.strip():
                    entries.append((text, placed))
    if not entries:
        fail("No non-empty link lines were added. Add at least one link using the format shown in the pull request template.")
        return False, lines
    if len(entries) > MAX_NEW_LINKS:
        fail(f"Found {len(entries)} added lines; the limit is {MAX_NEW_LINKS} links per pull request.")
        return False, lines

    # 4. per-link checks
    existing = {norm(u) for u in re.findall(r"\]\((https?://[^)\s]+)\)", base_readme)}
    seen = set()
    for text, placed in entries:
        m = ENTRY.match(text)
        if not m:
            fail("Use exactly `- [Name](https://example.com) — short description.`: "
                 f"`{safe(text)}`")
            continue
        name, url = m["name"], m["url"]
        errs = []
        detail = ""

        if not placed:
            errs.append("place the link inside the matching existing list in README.md")
        if EMBEDDED_LINK.search(f"{name} {m['desc']}"):
            errs.append("keep the name and description as plain text; do not embed extra links or HTML/Markdown")

        p = urlparse(url)
        host = (p.hostname or "").lower()
        try:
            port = p.port
        except ValueError:
            port = -1
        if p.username or p.password:
            errs.append("URL must not contain credentials")
        elif "." not in host or re.fullmatch(r"[\d.]+", host) or ":" in host:
            errs.append("use a public website domain; IP addresses are not accepted")
        elif is_shortener(host):
            errs.append("URL shorteners are not allowed; link directly to the destination")
        if port not in (None, 443):
            errs.append("use the standard HTTPS port")

        t = re.fullmatch(r"(?:https?://)?([\w.-]+\.[a-z]{2,})(?:/\S*)?", name.strip(), re.I)
        if t and norm_host(t.group(1)) != norm_host(host):
            errs.append("link text looks like a different website than the URL")

        key = norm(url)
        if key in existing or key in seen:
            errs.append("this URL is already listed or repeated in this pull request")
        seen.add(key)

        if not errs:
            host_err = resolve_fn(host)
            if host_err:
                errs.append(host_err)
            else:
                reachable, detail, page_text = reach_fn(url)
                if not reachable:
                    errs.append(f"link is not reachable ({detail})")
                elif page_text is None:
                    errs.append(f"page could not be screened automatically ({detail}); maintainer review is required")
                else:
                    findings = content_findings(f"{name} {m['desc']} {url} {page_text}")
                    if findings:
                        errs.append("restricted-content signals detected: " + ", ".join(findings))

        if errs:
            fail(f"**{safe(name, 60)}** — " + "; ".join(errs))
        else:
            good(f"**{safe(name, 60)}** — reachable; no restricted-content signals found ({detail})")

    return state["ok"], lines


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def write_report(ok, lines):
    head = (
        "<!-- awesome-links-pr-check -->\n"
        "### 🔎 Awesome Links · automated PR review\n\n"
        "The check reviews link format, duplicates, availability, and restricted-content signals.\n\n"
    )
    if ok:
        foot = (
            "\n\n**Result: approved.** All checks passed, so this pull request will be merged automatically. "
            "Content screening is best-effort and does not guarantee that a page is safe."
        )
    else:
        foot = (
            "\n\n**Action needed:** this pull request was not merged automatically. "
            "Please fix the items above and push an update. If automated screening could not read a page, "
            "ask a maintainer to review it manually."
        )
    with open("report.md", "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + foot + "\n")


def main():
    repo = os.environ["REPO"]
    pr = os.environ["PR_NUMBER"]
    sha = os.environ["HEAD_SHA"]

    try:
        meta = json.loads(gh("api", f"repos/{repo}/pulls/{pr}"))
        if meta["head"]["sha"] != sha:
            write_report(False, ["- ❌ The pull request changed during the check. A new run will review the latest commit."])
            sys.exit(1)
        listing = gh("api", f"repos/{repo}/pulls/{pr}/files", "--paginate",
                     "--jq", '.[] | [.filename, .status] | @tsv')
        changed = [tuple(row.split("\t")) for row in listing.splitlines() if row]
        diff = gh("api", "-H", "Accept: application/vnd.github.v3.diff", f"repos/{repo}/pulls/{pr}")
    except subprocess.CalledProcessError:
        write_report(False, ["- ❌ The check could not read the pull request changes. The pull request may be too large or the API request may have failed."])
        sys.exit(1)

    with open("README.md", encoding="utf-8") as f:
        base_readme = f.read()

    ok, lines = analyze(diff, changed, base_readme)
    write_report(ok, lines)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()