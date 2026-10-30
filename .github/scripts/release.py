import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
RELEASE_INTERVAL = timedelta(days=5)
VERSION_PATTERN = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def api_request(path, data=None):
    url = path if path.startswith("https://") else f"{API_ROOT}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = Request(url, data=body, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed ({error.code}): {detail}") from error


def api_pages(path):
    url = f"{API_ROOT}{path}"
    while url:
        request = Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                page = json.load(response)
                link_header = response.headers.get("Link", "")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"GitHub API request failed ({error.code}): {detail}"
            ) from error
        yield from page
        next_link = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
        url = next_link.group(1) if next_link else None


def bump_version(tag, release_type):
    match = VERSION_PATTERN.fullmatch(tag)
    if not match:
        raise ValueError(f"Latest release tag is not a plain semantic version: {tag}")
    major, minor, patch = (int(value) for value in match.groups())
    if release_type == "Major":
        return f"v{major + 1}.0.0"
    if release_type == "Minor":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def release_type_for(pull_requests):
    labels = {
        label["name"].lower()
        for pull_request in pull_requests
        for label in pull_request.get("labels", [])
    }
    if "release:major" in labels:
        return "Major"
    if "release:minor" in labels:
        return "Minor"
    return "Patch"


def clean_generated_notes(generated_notes):
    lines = generated_notes.strip().splitlines()
    if lines and lines[0].strip().lower() == "## what's changed":
        lines = lines[1:]
    return "\n".join(lines).strip()


def build_release_body(repository, owner, display_name, tag, previous_tag,
                       release_type, release_date, pull_requests, generated_notes):
    repo_name = repository["name"]
    repo_url = repository["html_url"]
    owner_url = f"https://github.com/{owner}"
    if pull_requests:
        summary = (
            f"This release includes {len(pull_requests)} merged pull request"
            f"{'s' if len(pull_requests) != 1 else ''} since {previous_tag}."
        )
        highlights = "\n".join(
            f"- {pull_request['title']} "
            f"[#{pull_request['number']}]({pull_request['html_url']})"
            for pull_request in pull_requests[:10]
        )
        if len(pull_requests) > 10:
            highlights += f"\n- And {len(pull_requests) - 10} more merged pull requests."
    else:
        summary = f"This release includes repository updates since {previous_tag}."
        highlights = "- Repository updates since the previous release."

    changelog_url = f"{repo_url}/compare/{previous_tag}...{tag}"
    return f"""# {repo_name} {tag}
**Release Type:** `{release_type}`
**Author:** [@{owner}]({owner_url})
**Date:** {release_date}
**Repository:** [{repo_name}]({repo_url})

---

## Summary
{summary}

---

## Highlights
{highlights}

---

## What's Next
- Continued community contributions via issues and pull requests
- Expanded the collection as new resources are reviewed

---

## What's Changed
{clean_generated_notes(generated_notes)}

**Full Changelog**: {changelog_url}

---

<p align="center">
  © {release_date[:4]} {display_name}. All rights reserved.
</p>
"""


def main():
    repository_name = os.environ["REPO"]
    encoded_repo = quote(repository_name, safe="/")
    repository = api_request(f"/repos/{encoded_repo}")
    default_branch = repository["default_branch"]
    latest_release = api_request(f"/repos/{encoded_repo}/releases/latest")
    previous_tag = latest_release["tag_name"]
    published_at = datetime.fromisoformat(
        latest_release["published_at"].replace("Z", "+00:00")
    )
    now = datetime.now(timezone.utc)
    if now - published_at < RELEASE_INTERVAL:
        print("The latest release is less than five days old; nothing to do.")
        return

    head = api_request(
        f"/repos/{encoded_repo}/commits/{quote(default_branch, safe='')}"
    )
    head_sha = head["sha"]
    comparison = api_request(
        f"/repos/{encoded_repo}/compare/"
        f"{quote(previous_tag, safe='')}...{quote(head_sha, safe='')}"
    )
    if comparison["ahead_by"] == 0:
        print("No changes since the latest release; nothing to do.")
        return

    changed_files = comparison.get("files", [])
    release_files = [
        file for file in changed_files
        if not file["filename"].startswith(".github/avatars/")
    ]
    if changed_files and not release_files:
        print("Only generated contributor avatars changed; nothing to release.")
        return

    included_commits = {commit["sha"] for commit in comparison.get("commits", [])}
    merged_after_release = []
    for pull_request in api_pages(
        f"/repos/{encoded_repo}/pulls?state=closed&sort=updated"
        "&direction=desc&per_page=100"
    ):
        merged_at = pull_request.get("merged_at")
        if merged_at and datetime.fromisoformat(
            merged_at.replace("Z", "+00:00")
        ) >= published_at and pull_request.get("merge_commit_sha") in included_commits:
            merged_after_release.append(pull_request)

    release_type = release_type_for(merged_after_release)
    tag = bump_version(previous_tag, release_type)
    notes = api_request(
        f"/repos/{encoded_repo}/releases/generate-notes",
        {
            "tag_name": tag,
            "target_commitish": head_sha,
            "previous_tag_name": previous_tag,
        },
    )["body"]
    owner = repository["owner"]["login"]
    profile = api_request(f"/users/{quote(owner, safe='')}")
    body = build_release_body(
        repository,
        owner,
        profile.get("name") or owner,
        tag,
        previous_tag,
        release_type,
        now.date().isoformat(),
        merged_after_release,
        notes,
    )
    api_request(
        f"/repos/{encoded_repo}/releases",
        {
            "tag_name": tag,
            "name": f"{repository['name']} {tag}",
            "body": body,
            "target_commitish": head_sha,
            "draft": False,
            "prerelease": False,
        },
    )
    print(f"Published {tag} as a {release_type.lower()} release.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, RuntimeError) as error:
        print(f"Release workflow failed: {error}", file=sys.stderr)
        raise
