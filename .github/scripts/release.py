import base64
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
README_LINK_PATTERN = re.compile(
    r"^\s*[-*]\s+\[([^\]]+)\]\((https?://[^)\s]+)\)"
    r"(?:\s+[-—]\s+(.+))?\s*$"
)


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


def release_is_due(published_at, now):
    return (now.date() - published_at.date()).days >= RELEASE_INTERVAL.days


def added_readme_links(previous_readme, current_readme):
    previous_urls = {
        match.group(2)
        for line in previous_readme.splitlines()
        if (match := README_LINK_PATTERN.match(line))
    }
    additions = []
    for line in current_readme.splitlines():
        match = README_LINK_PATTERN.match(line)
        if match and match.group(2) not in previous_urls:
            name, url, description = match.groups()
            entry = f"- [{name}]({url})"
            if description:
                entry += f" — {description}"
            additions.append((url, entry))
            previous_urls.add(url)
    return [entry for _, entry in additions]


def readme_at_ref(repository, ref):
    response = api_request(
        f"/repos/{quote(repository, safe='/')}/contents/README.md"
        f"?ref={quote(ref, safe='')}"
    )
    return base64.b64decode(response["content"]).decode("utf-8")


def release_section(title, entries, empty_message):
    content = "\n".join(entries) if entries else f"- {empty_message}"
    return f"## {title}\n{content}"


def body_excerpt(body, limit=240):
    lines = []
    for line in re.sub(r"<!--.*?-->", "", body or "", flags=re.DOTALL).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            if lines:
                break
            continue
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        lines.append(line)
    excerpt = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if len(excerpt) > limit:
        excerpt = excerpt[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    return excerpt


def build_release_body(repository, owner, display_name, tag, previous_tag,
                       release_type, release_date, pull_requests, issues,
                       commits, new_links):
    repo_name = repository["name"]
    repo_url = repository["html_url"]
    owner_url = f"https://github.com/{owner}"
    summary = (
        f"This release includes {len(new_links)} new README "
        f"{'link' if len(new_links) == 1 else 'links'}, "
        f"{len(pull_requests)} merged "
        f"{'pull request' if len(pull_requests) == 1 else 'pull requests'}, "
        f"{len(issues)} closed "
        f"{'issue' if len(issues) == 1 else 'issues'}, and "
        f"{len(commits)} standalone "
        f"{'commit' if len(commits) == 1 else 'commits'} since {previous_tag}."
    )
    pr_entries = [
        f"- [#{pr['number']}]({pr['html_url']}) — {pr['title']}"
        + (f": {body_excerpt(pr.get('body'))}" if body_excerpt(pr.get("body")) else "")
        for pr in pull_requests
    ]
    issue_entries = [
        f"- [#{issue['number']}]({issue['html_url']}) — {issue['title']}"
        + (
            f": {body_excerpt(issue.get('body'))}"
            if body_excerpt(issue.get("body"))
            else ""
        )
        for issue in issues
    ]
    commit_entries = [
        f"- [{commit['sha'][:7]}]({commit['html_url']}) — "
        f"{commit['commit']['message'].splitlines()[0]}"
        for commit in commits
    ]
    changelog_url = f"{repo_url}/compare/{previous_tag}...{tag}"
    sections = [
        release_section("New links added to README", new_links, "No new links."),
        release_section(
            "Merged pull requests", pr_entries, "No merged pull requests."
        ),
        release_section("Closed issues", issue_entries, "No issues closed."),
        release_section("Standalone commits", commit_entries, "No standalone commits."),
    ]
    change_details = "\n\n---\n\n".join(sections)
    return f"""# {repo_name} {tag}
**Release Type:** `{release_type}`
**Author:** [@{owner}]({owner_url})
**Date:** {release_date}
**Repository:** [{repo_name}]({repo_url})

---

## Summary
{summary}

---

{change_details}

**Full Changelog**: {changelog_url}

---

<p align="center">
  © {release_date[-4:]} {display_name}. All rights reserved.
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
    if not release_is_due(published_at, now):
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

    closed_issues = [
        issue
        for issue in api_pages(
            f"/repos/{encoded_repo}/issues?state=closed"
            f"&since={quote(published_at.isoformat(), safe='')}"
            "&sort=updated&direction=desc&per_page=100"
        )
        if "pull_request" not in issue
        and issue.get("closed_at")
        and datetime.fromisoformat(
            issue["closed_at"].replace("Z", "+00:00")
        ) >= published_at
    ]

    previous_readme = readme_at_ref(repository_name, previous_tag)
    current_readme = readme_at_ref(repository_name, head_sha)
    new_links = added_readme_links(previous_readme, current_readme)

    pull_request_commit_shas = set()
    for pull_request in merged_after_release:
        pull_request_commit_shas.update(
            commit["sha"]
            for commit in api_pages(
                f"/repos/{encoded_repo}/pulls/{pull_request['number']}/commits"
                "?per_page=100"
            )
        )
    standalone_commits = [
        commit
        for commit in comparison.get("commits", [])
        if commit["sha"] not in pull_request_commit_shas
    ]

    release_type = release_type_for(merged_after_release)
    tag = bump_version(previous_tag, release_type)
    owner = repository["owner"]["login"]
    profile = api_request(f"/users/{quote(owner, safe='')}")
    body = build_release_body(
        repository,
        owner,
        profile.get("name") or owner,
        tag,
        previous_tag,
        release_type,
        now.strftime("%d-%m-%Y"),
        merged_after_release,
        closed_issues,
        standalone_commits,
        new_links,
    )
    api_request(
        f"/repos/{encoded_repo}/releases",
        {
            "tag_name": tag,
            "name": tag,
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
