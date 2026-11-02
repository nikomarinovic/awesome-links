#!/usr/bin/env python3
"""Validate link-suggestion issues and add accepted resources to the README."""

import difflib
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

from pr_check import analyze, check_host, norm, reach


MARKER = "<!-- awesome-links-issue-bot -->"
RESOURCE_LABEL = "new resource"
MAX_LINKS = 10
CATEGORY_SECTIONS = {
    "ai": "ai",
    "developer": "developer-utilities",
    "developer utilities": "developer-utilities",
    "apis & backend": "apis--backend",
    "maps": "maps",
    "testing & prototyping": "testing--prototyping",
    "ui & design": "design-tools",
    "ui components": "ui-components",
    "design inspiration": "design-inspiration",
    "colors": "colors",
    "animation & motion": "animation--motion",
    "fonts & typography": "fonts--typography",
    "design tools": "design-tools",
    "learning & documentation": "learning--documentation",
    "certificates": "certificates",
    "repositories": "repositories",
    "apps": "apps",
    "miscellaneous": "miscellaneous",
}
CATEGORY_RULES = (
    ("AI", re.compile(r"\b(?:ai|artificial intelligence|machine learning|llm|mcp|chatbot|generative)\b", re.I)),
    ("Maps", re.compile(r"\b(?:maps?|mapping|geospatial|geojson)\b", re.I)),
    ("Testing & Prototyping", re.compile(r"\b(?:testing|test framework|prototype|simulator|simulation)\b", re.I)),
    ("APIs & Backend", re.compile(r"\b(?:api|backend|database|hosting|server|authentication)\b", re.I)),
    ("UI Components", re.compile(r"\b(?:ui component|component library|design system)\b", re.I)),
    ("Design Inspiration", re.compile(r"\b(?:inspiration|showcase|portfolio|gallery)\b", re.I)),
    ("Fonts & Typography", re.compile(r"\b(?:font|typography|typeface)\b", re.I)),
    ("Colors", re.compile(r"\b(?:color palette|colour palette|colors?|colours?)\b", re.I)),
    ("Animation & Motion", re.compile(r"\b(?:animation|motion design)\b", re.I)),
    ("Learning & Documentation", re.compile(r"\b(?:learning|tutorial|course|documentation|docs)\b", re.I)),
    ("Repositories", re.compile(r"\b(?:repository|repositories|open.source|github repo)\b", re.I)),
    ("Developer Utilities", re.compile(r"\b(?:developer|programming|coding|cli|debugging|formatter|converter|utility|utilities)\b", re.I)),
    ("Design Tools", re.compile(r"\b(?:design|designer|visual editor|wireframe)\b", re.I)),
    ("Apps", re.compile(r"\b(?:app|application|mobile app)\b", re.I)),
)


def gh(*args, input_text=None):
    result = subprocess.run(
        ["gh", *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {detail}")
    return result.stdout


def gh_api(path, method="GET", payload=None):
    args = ["api", path]
    if method != "GET":
        args.extend(["--method", method])
    if payload is not None:
        args.extend(["--input", "-"])
        return json.loads(gh(*args, input_text=json.dumps(payload)))
    output = gh(*args)
    return json.loads(output) if output.strip() else None


def issue_fields(body):
    fields = {}
    current = None
    for line in body.splitlines():
        heading = re.fullmatch(r"\s*###\s+(.+?)\s*", line)
        if heading:
            current = heading.group(1).strip().casefold().rstrip(":?")
            fields.setdefault(current, []).append([])
        elif current is not None:
            fields[current][-1].append(line)
    return {
        key: ["\n".join(value).strip() for value in values]
        for key, values in fields.items()
    }


def field_values(fields, *names):
    return [
        value
        for name in names
        for value in fields.get(name.casefold().rstrip(":?"), [])
        if value.strip()
    ]


def clean_lines(value):
    return [
        re.sub(r"^\s*(?:[-*]\s+|\d+[.)]\s+)", "", line).strip()
        for line in value.splitlines()
        if line.strip()
    ]


def urls_in(value):
    return [
        match.rstrip(".,;!?)']}")
        for match in re.findall(r"https?://[^\s<>]+", value)
    ]


def infer_category(name, url, rationale):
    evidence = f"{name} {url} {rationale}"
    for category, pattern in CATEGORY_RULES:
        if pattern.search(evidence):
            return category
    return "Miscellaneous"


def category_section(category):
    key = re.sub(r"\s+", " ", category.strip().casefold())
    return CATEGORY_SECTIONS.get(key)


def short_description(text, name):
    text = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]*>", " ", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -*\t\r\n")
    text = re.sub(
        rf"^(?:I\s+(?:built|created|made|developed)\s+)?{re.escape(name)}\s*[,:\-–—]\s*",
        "",
        text,
        flags=re.I,
    )
    first_sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    first_sentence = first_sentence.strip(" .")
    if len(first_sentence) > 220:
        first_sentence = first_sentence[:217].rsplit(" ", 1)[0].rstrip(" ,;:")
    if first_sentence:
        first_sentence = first_sentence[0].upper() + first_sentence[1:]
    return f"{first_sentence}." if len(first_sentence) >= 5 else ""


def page_description(url, name):
    host = urlparse(url).hostname or ""
    host_error = check_host(host)
    if host_error:
        raise ValueError(f"{name}: {host_error}")
    reachable, detail, page_text = reach(url)
    if not reachable or not page_text:
        raise ValueError(
            f"{name}: the page could not be read to write a description ({detail})"
        )
    description = short_description(page_text, name)
    if not description:
        raise ValueError(f"{name}: no usable description was found on the page")
    return description


def parse_resources(issue):
    fields = issue_fields(issue.get("body") or "")
    names = [
        line
        for field in field_values(fields, "resource name", "resource names")
        for line in clean_lines(field)
    ]
    urls = [
        url
        for field in field_values(fields, "resource url", "resource urls", "url")
        for url in urls_in(field)
    ]
    if not urls:
        raise ValueError("No resource URL was found in the issue form.")
    if len(urls) > MAX_LINKS:
        raise ValueError(f"Please submit no more than {MAX_LINKS} links at a time.")
    if not names:
        names = [urlparse(url).hostname or "" for url in urls]
    elif len(names) == 1 and len(urls) > 1:
        names = [
            names[0] if index == 0 else (urlparse(url).hostname or names[0])
            for index, url in enumerate(urls)
        ]
    if len(names) != len(urls):
        raise ValueError(
            "The number of resource names and URLs does not match. Put one name and one URL on each line."
        )

    categories = [
        line
        for field in field_values(fields, "category", "categories")
        for line in clean_lines(field)
    ]
    descriptions = field_values(
        fields,
        "why should it be included?",
        "why should they be included?",
        "description",
    )
    rationale_lines = [
        line
        for field in descriptions
        for line in clean_lines(field)
    ]
    if len(rationale_lines) == 1:
        rationale_lines *= len(urls)
    elif len(rationale_lines) != len(urls):
        rationale_lines = [" ".join(rationale_lines)] * len(urls)

    resources = []
    for index, (name, url) in enumerate(zip(names, urls)):
        name = name.strip()
        if not name or len(name) > 60 or any(char in name for char in "[]\n\r"):
            raise ValueError(f"Resource name {index + 1} is empty or not suitable for a README link.")
        if not url.startswith("https://"):
            raise ValueError(f"{name}: only direct HTTPS URLs can be added.")
        rationale = rationale_lines[index].strip() if rationale_lines else ""
        description = short_description(rationale, name) if rationale else ""
        if not description:
            description = page_description(url, name)
        if categories:
            category = categories[index] if len(categories) == len(urls) else categories[0]
            if len(categories) not in (1, len(urls)):
                raise ValueError("Provide one category or one category per resource.")
            if not category_section(category):
                category = infer_category(name, url, f"{rationale} {description}")
        else:
            category = infer_category(name, url, f"{rationale} {description}")
        if category == "Miscellaneous" and not categories:
            category_note = "Category was not specified or could not be inferred; used Miscellaneous."
        else:
            category_note = ""
        resources.append(
            {
                "name": name,
                "url": url,
                "category": category,
                "section": category_section(category),
                "description": description,
                "category_note": category_note,
            }
        )
    return resources


def add_resources(readme, resources):
    existing_urls = {
        norm(url)
        for url in re.findall(r"\]\((https://[^)\s]+)\)", readme)
    }
    additions = []
    for resource in resources:
        key = norm(resource["url"])
        if key in existing_urls:
            continue
        existing_urls.add(key)
        additions.append(resource)

    updated = readme
    for resource in additions:
        lines = updated.splitlines()
        start = None
        for index, line in enumerate(lines):
            if re.search(
                rf"<(?:div|h[1-6])\s+id=[\"']{re.escape(resource['section'])}[\"']",
                line,
                flags=re.I,
            ):
                start = index + 1
                break
        if start is None:
            raise ValueError(
                f"README section for category {resource['category']} was not found."
            )
        end = len(lines)
        for index in range(start, len(lines)):
            if re.search(r"<(?:div|h[1-6])\s+id=[\"'][^\"']+[\"']", lines[index], re.I):
                end = index
                break
        list_items = [
            index
            for index in range(start, end)
            if re.match(r"^\s*-\s+\[", lines[index])
        ]
        if not list_items:
            raise ValueError(
                f"README category {resource['category']} has no existing resource list."
            )
        entry = (
            f"- [{resource['name']}]({resource['url']}) — "
            f"{resource['description']}"
        )
        lines.insert(list_items[-1] + 1, entry)
        updated = "\n".join(lines) + ("\n" if readme.endswith("\n") else "")
    return updated, additions


def gh_label_name(labels, name):
    if any(label["name"].casefold() == name.casefold() for label in labels):
        return
    gh_api(
        f"repos/{os.environ['REPO']}/labels",
        "POST",
        {"name": name, "color": "2da44e", "description": "Automated link suggestion"},
    )


def ensure_issue_assigned(issue, owner):
    assignees = [assignee["login"] for assignee in issue.get("assignees", [])]
    if owner not in assignees:
        gh_api(
            f"repos/{os.environ['REPO']}/issues/{issue['number']}/assignees",
            "POST",
            {"assignees": [owner]},
        )


def add_issue_label(issue_number, label):
    gh_api(
        f"repos/{os.environ['REPO']}/issues/{issue_number}/labels",
        "POST",
        {"labels": [label]},
    )


def comment(issue_number, text):
    body = f"{MARKER}\n{text}"
    previous = last_bot_comment(issue_number)
    if previous:
        gh_api(
            f"repos/{os.environ['REPO']}/issues/comments/{previous['id']}",
            "PATCH",
            {"body": body},
        )
    else:
        gh_api(
            f"repos/{os.environ['REPO']}/issues/{issue_number}/comments",
            "POST",
            {"body": body},
        )


def last_bot_comment(issue_number):
    comments = gh_api(
        f"repos/{os.environ['REPO']}/issues/{issue_number}/comments"
    )
    return next(
        (item for item in reversed(comments) if MARKER in item.get("body", "")),
        None,
    )


def validate_resources(readme, updated_readme, reach_fn=reach, resolve_fn=check_host):
    diff = "diff --git a/README.md b/README.md\n" + "".join(
        difflib.unified_diff(
            readme.splitlines(keepends=True),
            updated_readme.splitlines(keepends=True),
            fromfile="a/README.md",
            tofile="b/README.md",
        )
    )
    return analyze(
        diff,
        [("README.md", "modified")],
        readme,
        reach_fn=reach_fn,
        resolve_fn=resolve_fn,
    )


def commit_readme(issue_number, readme, updated_readme):
    subprocess.run(["git", "fetch", "origin", "main"], check=True)
    subprocess.run(["git", "merge", "--ff-only", "FETCH_HEAD"], check=True)
    with open("README.md", encoding="utf-8") as file:
        current_readme = file.read()
    if current_readme != readme:
        raise RuntimeError(
            "The README changed during review. Please rerun the issue automation."
        )
    if current_readme == updated_readme:
        return False
    with open("README.md", "w", encoding="utf-8") as file:
        file.write(updated_readme)

    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        [
            "git", "config", "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        check=True,
    )
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(
        ["git", "commit", "-m", f"docs: add resources from issue #{issue_number}"],
        check=True,
    )
    subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    return True


def close_issue(issue_number):
    gh_api(
        f"repos/{os.environ['REPO']}/issues/{issue_number}",
        "PATCH",
        {"state": "closed", "state_reason": "completed"},
    )


def main():
    repo = os.environ["REPO"]
    issue_number = os.environ["ISSUE_NUMBER"]
    owner = os.environ["OWNER_LOGIN"]
    issue = gh_api(f"repos/{repo}/issues/{issue_number}")
    if issue.get("state") != "open" or "pull_request" in issue:
        print(f"Issue #{issue_number} is not an open issue; nothing to do.")
        return

    ensure_issue_assigned(issue, owner)
    title = issue.get("title", "")
    fields = issue_fields(issue.get("body") or "")
    is_suggestion = (
        title.casefold().startswith("link suggestion:")
        or (
            field_values(fields, "resource name", "resource names")
            and field_values(fields, "resource url", "resource urls")
        )
    )
    if not is_suggestion:
        comment(
            issue["number"],
            f"Assigned to @{owner}. I only process link suggestions, so I have "
            "not changed this issue. The maintainer will review it.",
        )
        return

    gh_label_name(
        gh_api(f"repos/{repo}/labels?per_page=100"),
        RESOURCE_LABEL,
    )
    add_issue_label(issue["number"], RESOURCE_LABEL)

    committed = False
    try:
        resources = parse_resources(issue)
        with open("README.md", encoding="utf-8") as file:
            readme = file.read()
        updated_readme, additions = add_resources(readme, resources)
        if not additions:
            previous = last_bot_comment(issue["number"])
            if previous and "README was updated by this issue" in previous["body"]:
                close_issue(issue_number)
                comment(
                    issue["number"],
                    f"Assigned to @{owner} and labeled **{RESOURCE_LABEL}**. "
                    "The resource was already added to the README by this issue; "
                    "closing it as completed.",
                )
                return
            comment(
                issue["number"],
                f"Assigned to @{owner} and labeled **{RESOURCE_LABEL}**. "
                "All suggested URLs are already in the README, so I did not "
                "make changes or close the issue. The maintainer can review it.",
            )
            return
        valid, findings = validate_resources(readme, updated_readme)
        if not valid:
            details = "\n".join(findings)
            comment(
                issue["number"],
                f"Assigned to @{owner} and labeled **{RESOURCE_LABEL}**. "
                "I could not safely add this suggestion automatically, so I "
                f"left the issue open for your review:\n\n{details}",
            )
            return
        committed = commit_readme(issue_number, readme, updated_readme)
        if not committed:
            comment(
                issue["number"],
                f"Assigned to @{owner} and labeled **{RESOURCE_LABEL}**. "
                "The suggested links were already present; I made no changes "
                "and left the issue open for your review.",
            )
            return
        names = ", ".join(item["name"] for item in additions)
        categories = ", ".join(dict.fromkeys(item["category"] for item in additions))
        comment(
            issue["number"],
            f"Assigned to @{owner} and labeled **{RESOURCE_LABEL}**. "
            f"README was updated by this issue: I added {names} under "
            f"{categories}. The automated link and content checks passed. "
            "Closing this issue as completed.",
        )
        close_issue(issue_number)
        print(f"Added {names} to README and closed issue #{issue_number}.")
        return
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        if committed:
            message = (
                f"Assigned to @{owner} and labeled **{RESOURCE_LABEL}**. "
                "README was updated by this issue, but I could not close it "
                f"automatically: {error} The maintainer can close it manually."
            )
        else:
            message = (
                f"Assigned to @{owner} and labeled **{RESOURCE_LABEL}**. "
                f"I could not safely prepare this link suggestion automatically: {error} "
                "The issue is still open for maintainer review."
            )
        comment(issue["number"], message)
        print(f"Could not process issue #{issue_number}: {error}", file=sys.stderr)
        return


if __name__ == "__main__":
    try:
        main()
    except (KeyError, json.JSONDecodeError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Issue automation failed: {error}", file=sys.stderr)
        raise
