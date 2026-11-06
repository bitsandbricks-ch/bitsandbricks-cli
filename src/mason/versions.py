"""Versions: bump a repo's version in a PR, and release it by pushing the tag v<version>.

Where a repo keeps its version, first match wins (spec):

1. the repo's own .mason.toml (only its [release] table is read):

    [release]
    version = "pyproject.toml"              # plugin.yaml, package.json, Cargo.toml, VERSION, src/app/__init__.py
    changelog = ["CHANGELOG.md"]            # optional; Keep a Changelog [Unreleased] or "## 1.2.0" sections
    tag = "v{version}"                      # optional tag format

2. its group in the defaults' mason.toml (version = "plugin.yaml", changelog = [...]);
3. a known file in the repo root that holds a version (plugin.yaml, pyproject.toml, package.json,
   Cargo.toml, VERSION), with CHANGELOG.md as its changelog when there is one.

`mason bump name [major|minor|patch|1.4.0]` opens a PR on bump/v<new> with the new version and a
changelog section; `mason release [names]` pushes the tag v<version> on the default branch for every
repo whose version is newer than its latest release. The repo's own workflow does the rest (build,
approval, publish), which `mason runs` and the Releases tab follow.
"""

from __future__ import annotations

import re
import time

from mason import core
from mason import repos as R
from mason.config import Config
from mason.core import WEB, api, get_file, newest, open_pr, say, version_tuple

PATTERNS = {  # file kind -> the version, with what comes before and after it
    "yaml": re.compile(r"""^(version:\s*["']?)([^"'\s#]+)(["']?)""", re.M),
    "toml": re.compile(r'^(version\s*=\s*")([^"]+)(")', re.M),
    "json": re.compile(r'^(\s*"version"\s*:\s*")([^"]+)(")', re.M),
    "python": re.compile(r"""^(__version__\s*=\s*["'])([^"']+)(["'])""", re.M),
    "plain": re.compile(r"\A(\s*v?)([0-9][^\s]*)(\s*)"),
}
REPO_FILE = ".mason.toml"  # a repo's own [release] table: version, changelog, tag
DETECT = ("plugin.yaml", "pyproject.toml", "package.json", "Cargo.toml", "VERSION")  # tried in this order
CHANGELOGS = ("CHANGELOG.md", "CHANGES.md", "HISTORY.md")


def kind(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith((".yaml", ".yml")):
        return "yaml"
    if name.endswith(".toml"):
        return "toml"
    if name.endswith(".json"):
        return "json"
    if name.endswith(".py"):
        return "python"
    return "plain"


def read_version(text: str | None, path: str) -> str | None:
    m = PATTERNS[kind(path)].search(text or "")
    return m.group(2) if m else None


def write_version(text: str, path: str, new: str) -> str:
    return PATTERNS[kind(path)].sub(lambda m: m.group(1) + new + m.group(3), text, count=1)


def is_level(word: str) -> bool:
    return word in ("major", "minor", "patch") or bool(re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", word))


def next_version(current: str, level: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", level):
        return level
    major, minor, patch = version_tuple(current)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise RuntimeError(f"bump takes major, minor, patch or a version like 1.4.0, not {level!r}")


def with_changelog(text: str | None, version: str, day: str, notes: list[str]) -> str | None:
    """The changelog with a section for `version`; None when it has one already.

    Keep a Changelog: the [Unreleased] entries become `## [version] - day`. Otherwise a
    `## version` section with `notes` goes on top."""
    text = text or "# Changelog\n"
    if re.search(rf"^##\s*\[?{re.escape(version)}\]?(\s|$)", text, re.M):
        return None
    m = re.search(r"^##\s*\[Unreleased\][^\n]*\n", text, re.M | re.I)
    if m:
        return text[:m.end()] + f"\n## [{version}] - {day}\n" + text[m.end():]
    section = f"## {version}\n\n" + "\n".join(notes or ["- "]) + "\n\n"
    m = re.search(r"^## ", text, re.M)
    return text[:m.start()] + section + text[m.start():] if m else text.rstrip("\n") + "\n\n" + section


_specs = core.Scoped("version_spec")  # repo -> spec() (None when there is no version file)


def repo_release_table(repo: str, names: set[str]) -> dict:
    """The [release] table of the repo's .mason.toml ({} without one). Only release settings are read here:
    groups and checks stay with the defaults and the org's .mason repo."""
    from mason.config import tomllib
    if REPO_FILE not in names:
        return {}
    text = core.file_on(repo, REPO_FILE) or ""
    try:
        table = tomllib.loads(text).get("release") or {}
    except tomllib.TOMLDecodeError as err:
        raise RuntimeError(f"{repo}/{REPO_FILE}: {err}") from err
    return table if isinstance(table, dict) else {}


def root_names(repo: str) -> set[str]:
    try:
        return {f.get("name", "") for f in api("GET", f"/repos/{repo}/contents/") or [] if isinstance(f, dict)}
    except core.RateLimited:
        raise
    except RuntimeError:
        return set()


def spec(cfg: Config, repo: str, look: bool = True) -> dict | None:
    """Where the repo keeps its version: {"file", "changelog": [...], "tag": "v{version}", "from"}.

    The repo's .mason.toml [release] table wins, then its group in the defaults (version = "..."), then a
    guess from known files in the repo root (plugin.yaml, pyproject.toml, package.json, Cargo.toml, VERSION)
    that hold a version. `look=False` never calls GitHub: the group's setting or what an earlier look found
    (for the TUI's key hints)."""
    g = R.group_of(cfg, repo)
    group = {"file": g.version, "changelog": list(g.changelog), "tag": "v{version}", "from": "group"} \
        if g and g.version else None
    if repo in _specs:
        return _specs[repo]
    if not look or repo.startswith("gitlab:"):
        return group
    names = root_names(repo)
    table = repo_release_table(repo, names)
    found = None
    if table.get("version"):
        logs = table.get("changelog", group["changelog"] if group else [])
        found = {"file": str(table["version"]), "changelog": [logs] if isinstance(logs, str) else list(logs),
                 "tag": str(table.get("tag") or "v{version}"), "from": REPO_FILE}
    elif group:
        found = group
    else:
        for name in DETECT:
            if name in names and read_version(core.file_on(repo, name), name):
                found = {"file": name, "changelog": [c for c in CHANGELOGS if c in names][:1], "tag": "v{version}",
                         "from": "detected"}
                break
    if found and "{version}" not in found["tag"]:
        raise RuntimeError(f"{repo}/{REPO_FILE}: tag must contain {{version}}, e.g. \"v{{version}}\"")
    _specs[repo] = found
    return found


def source(cfg: Config, repo: str, look: bool = True) -> tuple[str, list[str]] | None:
    """(version file, changelog files) of the repo (see spec), or None when it has no version file."""
    sp = spec(cfg, repo, look)
    return (sp["file"], sp["changelog"]) if sp else None


def tag_of(cfg: Config, repo: str, version: str) -> str:
    sp = spec(cfg, repo) or {}
    return (sp.get("tag") or "v{version}").format(version=version)


def release_tags(repo: str) -> list[str]:
    return [r["tag_name"] for r in api("GET", f"/repos/{repo}/releases?per_page=100") or [] if not r.get("draft")]


def tag_exists(repo: str, tag: str) -> bool:
    try:
        api("GET", f"/repos/{repo}/git/ref/tags/{tag}")
        return True
    except core.RateLimited:
        raise
    except RuntimeError as err:
        if "404" in str(err) or "Not Found" in str(err):
            return False
        raise


def merged_since(repo: str, base: str, tag: str | None) -> list[dict]:
    """PRs merged into `base` since the release `tag` was published (all of them without a tag)."""
    since = ""
    if tag:
        since = (api("GET", f"/repos/{repo}/releases/tags/{tag}") or {}).get("published_at") or ""
    pulls = api("GET", f"/repos/{repo}/pulls?state=closed&base={base}&sort=updated&direction=desc&per_page=50") or []
    out = [p for p in pulls if p.get("merged_at") and p["merged_at"] > since
           and not re.match(r"(?i)release \d", p.get("title") or "")]
    return sorted(out, key=lambda p: p["merged_at"])


def state(cfg: Config, repo: str) -> dict:
    """The version on the default branch against the latest release: what `release` would do."""
    src = source(cfg, repo)
    if not src:
        return {"repo": repo, "file": None}
    path = src[0]
    base = api("GET", f"/repos/{repo}")["default_branch"]
    ver = read_version(core.file_on(repo, path, base), path)
    last = newest(release_tags(repo))
    todo = bool(ver) and (not last or version_tuple(ver) > version_tuple(last))
    return {"repo": repo, "file": path, "base": base, "version": ver, "released": last,
            "tag": tag_of(cfg, repo, ver) if ver else None, "todo": todo, "from": (spec(cfg, repo) or {}).get("from")}


def bump(cfg: Config, repo: str, level: str, auto: bool = False) -> None:
    src = source(cfg, repo)
    if not src:
        say('   no version file found (set [release] version = "..." in .mason.toml, or version on its group), skipped')
        return
    path, changelogs = src
    base = api("GET", f"/repos/{repo}")["default_branch"]
    found = get_file(repo, path, base)
    cur = read_version(found[0] if found else None, path)
    if not cur:
        say(f"   no version in {path}, skipped")
        return
    new = next_version(cur, level)
    if version_tuple(new) <= version_tuple(cur):
        raise RuntimeError(f"{new} is not newer than {cur} in {path}")
    last = newest(release_tags(repo))
    if last and version_tuple(cur) > version_tuple(last):
        say(f"   note: {cur} isn't released yet (latest {last}); `mason release {core.short(repo)}` would publish it")
    notes = [f"- {p['title']} (#{p['number']})" for p in merged_since(repo, base, last)]
    files = {path: (write_version(found[0], path, new), found[1])}
    day = time.strftime("%Y-%m-%d")
    for cl in changelogs:
        got = get_file(repo, cl, base)
        text = with_changelog(got[0] if got else None, new, day, notes)
        if text:
            files[cl] = (text, got[1] if got else None)
    branch = f"bump/v{new}"  # not release/*: release workflows often trust release branches
    say(f"   {cur} -> {new} ({', '.join(files)}), {len(notes)} PR(s) merged since {last or 'the start'}")
    for line in notes:
        say(f"      {line}")
    if core.dry_run():
        say(f"   [dry-run] branch {branch}, PR 'Release {new}'")
        return
    logs = ", ".join(f"`{c}`" for c in files if c != path)
    body = (f"Sets `{path}` to {new}" + (f" and adds the {new} section to {logs}" if logs else "")
            + f". Edit the changelog here before merging.\n\nAfter the merge, `mason release {core.short(repo)}` "
            f"(R in the TUI) pushes the tag {tag_of(cfg, repo, new)}; the repo's release workflow takes it from there.")
    url = open_pr(repo, base, branch, files, f"Release {new}", f"Release {new}", body)
    say(f"   opened {url}")
    if auto:
        R.allow_auto_merge(repo)
        merged, why = R.auto_merge(repo, int(url.rsplit("/", 1)[-1]), "squash")
        say(f"   auto-merge not set: {why.strip()[:200]}" if why else "   merged right away" if merged
            else "   auto-merge on")


def release(cfg: Config, repo: str, yes: bool = True) -> None:
    """Push v<version> on the default branch when it is new; otherwise say where the release stands.
    `yes=False` asks before pushing the tag."""
    st = state(cfg, repo)
    if not st["file"]:
        say('   no version file found (set [release] version = "..." in .mason.toml, or version on its group), skipped')
        return
    if not st["version"]:
        say(f"   no version in {st['file']}, skipped")
        return
    tag, page = st["tag"], f"{WEB}/{repo}/releases/tag/{st['tag']}"
    if tag_exists(repo, tag):
        run = release_run(repo, tag)
        done = st["released"] == tag
        say(f"   {tag} " + ("is released" if done else "is tagged; the release appears once its workflow is done"))
        say(f"      {page}")
        if run:
            say(f"      release run: {R.run_state(run)}  {run.get('html_url', '')}")
            if run.get("conclusion") in R.FAILED:
                say(f"      it failed: mason rerun {core.short(repo)} --run {run['id']}")
        return
    if not st["todo"]:
        say(f"   {st['file']} says {st['version']}, not newer than {st['released']}: "
            f"bump first (mason bump {core.short(repo)})")
        return
    changelogs = (source(cfg, repo) or ("", []))[1]
    if changelogs and "-" not in st["version"]:
        text = core.file_on(repo, changelogs[0], st["base"]) or ""
        if not re.search(rf"^##\s*\[?{re.escape(st['version'])}\]?(\s|$)", text, re.M):
            say(f"   {changelogs[0]} has no {st['version']} section: bump first (mason bump {core.short(repo)})")
            return
    if core.dry_run():
        say(f"   [dry-run] push tag {tag} on {st['base']} (latest {st['released'] or 'none'})")
        return
    if not yes:
        try:
            answer = input(f"   Push the tag {tag} on {st['base']} (latest release {st['released'] or 'none'})? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            say("   left as it is")
            return
    head = api("GET", f"/repos/{repo}/git/ref/heads/{st['base']}")["object"]["sha"]
    api("POST", f"/repos/{repo}/git/refs", {"ref": f"refs/tags/{tag}", "sha": head})
    say(f"   tagged {tag} on {st['base']} at {head[:7]} (was {st['released'] or 'none'})")
    run = release_run(repo, tag, wait=20)
    say(f"      release run: {R.run_state(run)}  {run.get('html_url', '')}" if run
        else f"      no release run started yet: {WEB}/{repo}/actions")
    say("      follow it in Runs (3); a waits for your approval, x re-runs failed jobs")


def bump_all(cfg: Config, repos: list[str], level: str, auto: bool = False) -> None:
    for repo in repos:
        say(f"== {repo}")
        bump(cfg, repo, level, auto)


def release_all(cfg: Config, repos: list[str], yes: bool = False) -> None:
    for repo in repos:
        say(f"== {repo}")
        release(cfg, repo, yes)


def release_run(repo: str, tag: str, wait: int = 0) -> dict | None:
    """The workflow run the tag started; waits up to `wait` seconds for it to show up."""
    deadline = time.time() + wait
    while True:
        found = (api("GET", f"/repos/{repo}/actions/runs?branch={tag}&per_page=10") or {}).get("workflow_runs") or []
        run = next((r for r in found if r.get("head_branch") == tag), None)
        if run or time.time() >= deadline:
            return run
        time.sleep(3)


def summary(cfg: Config, repos: list[str]) -> None:
    """mason release --list: the version of every repo against its latest release."""
    rows = []
    for repo in repos:
        st = state(cfg, repo)
        if st["file"]:
            rows.append([core.short(repo), st["file"], st["version"] or "-", st["released"] or "-",
                         st["tag"] if st["todo"] else ""])
    if not rows:
        say("no repo has a version file (set `version = ...` on a group in the defaults)")
        return
    core.table(["repo", "file", "version", "released", "to release"], rows)

