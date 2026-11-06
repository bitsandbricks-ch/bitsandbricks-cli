"""Releases: create one by hand, and show a repo's releases in detail.

`mason release new name v1.4.0 [--title T] [--notes TEXT|-] [--draft] [--prerelease] [--target REF]
[--generate-notes]` creates a GitHub release (and its tag, on the default branch or --target, when the
tag is new). `mason release view name [tag]` shows one release in full with the repo's recent releases;
the TUI's and the web app's release pages show the same text.

The version-file flow (`mason bump`, then `mason release name` pushing v<version>) stays in versions.py:
this is for releases that aren't driven by a version file, drafts and pre-releases.
"""

from __future__ import annotations

import fnmatch
import re

from mason import core
from mason import repos as R
from mason.config import Config
from mason.core import WEB, ago, api, say

TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+/-]{0,99}$")
RECENT = 20  # releases a detail page lists


def valid_tag(tag: str) -> str:
    """A tag name git and GitHub accept (no spaces, no `..`, no leading dash or trailing slash/dot)."""
    tag = (tag or "").strip()
    if not tag:
        raise RuntimeError("a release needs a tag, e.g. v1.4.0")
    if not TAG.match(tag) or ".." in tag or tag.endswith((".", "/", ".lock")) or "//" in tag:
        raise RuntimeError(f"not a tag name: {tag[:60]!r}")
    return tag


def kind(rel: dict) -> str:
    return "draft" if rel.get("draft") else "prerelease" if rel.get("prerelease") else "published"


def recent(repo: str, n: int = RECENT) -> list[dict]:
    """The repo's newest releases, drafts too (you see them where you may push). One REST call."""
    return api("GET", f"/repos/{repo}/releases?per_page={n}") or []


def find(rels: list[dict], tag: str | None) -> dict | None:
    if not rels:
        return None
    if not tag:
        return rels[0]
    return next((r for r in rels if r.get("tag_name") == tag), None)


def runs_for(runs: list[dict], tag: str) -> list[dict]:
    """The workflow runs a tag started (their head_branch is the tag)."""
    return [r for r in runs if r.get("head_branch") == tag]


def missing_assets(cfg: Config | None, repo: str, rel: dict) -> list[str]:
    group = (cfg.group_of(repo) if repo.startswith("gitlab:") else R.group_of(cfg, repo)) if cfg else None
    if not group or not rel:
        return []
    names = [a.get("name") or "" for a in rel.get("assets") or []]
    return [g for g in group.release_assets if not any(fnmatch.fnmatchcase(n, g) for n in names)]


def row(rel: dict) -> dict:
    """One line of a repo's release list, for the web page and the TUI."""
    assets = rel.get("assets") or []
    return {"tag": rel.get("tag_name") or "", "title": rel.get("name") or "", "state": kind(rel),
            "when": ago(rel.get("published_at") or rel.get("created_at")),
            "assets": len(assets), "url": rel.get("html_url") or "",
            "author": (rel.get("author") or {}).get("login") or ""}


def detail_text(cfg: Config | None, repo: str, rel: dict | None, runs: list[dict], others: list[dict]) -> str:
    """One release in full: state, title, target, assets (and missing ones), notes, the runs of its tag,
    then the repo's other recent releases."""
    lines = [repo, ""]
    if not rel:
        lines.append("no release yet: n creates the first one")
    else:
        tag = rel.get("tag_name") or ""
        title = rel.get("name") or ""
        lines.append(f"{tag} · {kind(rel)} · {ago(rel.get('published_at') or rel.get('created_at'))}"
                     + (f" · by {rel['author']['login']}" if (rel.get("author") or {}).get("login") else ""))
        if title and title != tag:
            lines.append(title)
        if rel.get("html_url"):
            lines.append(rel["html_url"])
        if rel.get("target_commitish"):
            lines.append(f"target       {rel['target_commitish']}")
        assets = rel.get("assets") or []
        lines += ["", f"assets ({len(assets)})" if assets else "no assets"]
        for a in assets:
            size = f"  {a['size'] / 1024:.0f} KB" if a.get("size") else ""
            downloads = f"  {a['download_count']} downloads" if "download_count" in a else ""
            lines.append(f"  {a.get('name')}{size}{downloads}" + (f"  {a['url']}" if a.get("url") else ""))
        for g in missing_assets(cfg, repo, rel):
            lines.append(f"  MISSING {g} (the group's release_assets)")
        notes = [line.rstrip() for line in (rel.get("body") or "").strip().splitlines()]
        lines += ["", "notes" if notes else "no notes", *[f"  {line}" for line in notes]]
        tag_runs = runs_for(runs, tag)
        lines += ["", f"runs of {tag}" if tag_runs else f"no runs of {tag} in the last runs"]
        for r in tag_runs[:5]:
            lines.append(f"  {R.run_state(r):<10} {r.get('name')} · {ago(r.get('created_at'))}")
            if r.get("html_url"):
                lines.append(f"    {r['html_url']}")
    rest = [r for r in others if r is not rel]
    if rest:
        lines += ["", "other releases"]
        for r in rest:
            x = row(r)
            title = x["title"] if x["title"] != x["tag"] else ""
            lines.append(f"  {x['tag']:<16} {x['state']:<10} {x['when']:<10} {title}".rstrip())
    return "\n".join(lines)


def page(cfg: Config | None, repo: str, tag: str | None = None) -> dict:
    """What a release page shows (GitHub): the release list, the selected release and its text."""
    rels = recent(repo)
    try:
        runs = (api("GET", f"/repos/{repo}/actions/runs?per_page=30") or {}).get("workflow_runs") or []
    except core.RateLimited:
        raise
    except RuntimeError:
        runs = []
    rel = find(rels, tag)
    if tag and rel is None:
        try:
            rel = api("GET", f"/repos/{repo}/releases/tags/{valid_tag(tag)}")
        except core.RateLimited:
            raise
        except RuntimeError as err:
            raise RuntimeError(f"no release {tag} in {repo}") from err
    rel_url = (rel or {}).get("html_url") or f"{WEB}/{repo}/releases"
    return {"repo": repo, "provider": "github", "releases": [row(r) for r in rels],
            "selected": (rel or {}).get("tag_name") or "", "url": rel_url,
            "text": detail_text(cfg, repo, rel, runs, rels), "suggest": suggest_from(rels)}


def view(cfg: Config, repo: str, tag: str | None = None) -> None:
    """mason release view name [tag]."""
    say(page(cfg, repo, tag)["text"])


def suggest_from(rels: list[dict]) -> str:
    """The next patch tag after the newest published release (v0.1.0 when there is none)."""
    from mason import versions as V
    tags = [r.get("tag_name") or "" for r in rels if not r.get("draft")]
    last = core.newest([t for t in tags if re.match(r"^v?\d", t)])
    if not last:
        return "v0.1.0"
    prefix = "v" if last.startswith("v") else ""
    return prefix + V.next_version(last.lstrip("v").split("-")[0], "patch")


def tag_exists(repo: str, tag: str) -> bool:
    from mason import versions as V
    return V.tag_exists(repo, tag)


def exists(repo: str, tag: str) -> dict | None:
    """The release of `tag`, or None."""
    try:
        return api("GET", f"/repos/{repo}/releases/tags/{tag}") or None
    except core.RateLimited:
        raise
    except RuntimeError as err:
        if "404" in str(err) or "Not Found" in str(err):
            return None
        raise


def create(repo: str, tag: str, title: str = "", notes: str = "", draft: bool = False, prerelease: bool = False,
           target: str = "", generate: bool = False) -> str:
    """Create a GitHub release; GitHub creates the tag on `target` (the default branch) when it is new.
    Returns the release's URL ('' in a dry run)."""
    tag = valid_tag(tag)
    title = (title or "").strip()[:200] or tag
    target = (target or "").strip()
    if target and not re.match(r"^[A-Za-z0-9._/-]{1,250}$", target):
        raise RuntimeError(f"not a branch or commit: {target[:60]!r}")
    if draft and prerelease:
        say("   a draft that is also a pre-release: GitHub keeps both flags")
    got = exists(repo, tag)
    if got:
        raise RuntimeError(f"{repo} has a release {tag} already: {got.get('html_url', '')}")
    new_tag = not tag_exists(repo, tag)
    base = target or (api("GET", f"/repos/{repo}") or {}).get("default_branch", "")
    what = kind({"draft": draft, "prerelease": prerelease})
    where = f"a new tag on {base or 'the default branch'}" if new_tag else "the existing tag"
    body = {"tag_name": tag, "name": title, "body": notes or "", "draft": bool(draft), "prerelease": bool(prerelease)}
    if target:
        body["target_commitish"] = target
    if generate:
        body["generate_release_notes"] = True
    if core.dry_run():
        api("POST", f"/repos/{repo}/releases", body)
        say(f"   [dry-run] create the {what} release {tag} ({title}) of {repo} from {where}")
        return ""
    rel = api("POST", f"/repos/{repo}/releases", body) or {}
    R.forget(repo)
    url = rel.get("html_url") or f"{WEB}/{repo}/releases/tag/{tag}"
    say(f"   created the {what} release {tag} of {repo} from {where}")
    say(f"      {url}")
    if new_tag and not draft:
        say("      the new tag may start the repo's release workflow: follow it in Runs (3)")
    return url
