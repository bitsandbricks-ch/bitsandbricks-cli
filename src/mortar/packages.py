"""Container images (packages on GHCR): the newest tags of each image, whether the latest release
has its image, and failed image builds.

Reading packages needs the `read:packages` scope: `gh auth refresh -s read:packages`.
"""

from __future__ import annotations

from urllib.parse import quote

from mortar import core
from mortar import repos as R
from mortar.config import Config
from mortar.core import ago, api, newest, say, short, table

SCOPE_HINT = "packages need the read:packages scope: gh auth refresh -s read:packages"
IMAGE_WORDS = ("image", "docker", "container")


def is_image_run(run: dict) -> bool:
    """A run of a workflow that builds images: named or filed like one."""
    text = f"{run.get('name') or ''} {run.get('path') or ''}".lower()
    return any(w in text for w in IMAGE_WORDS)


def _missing(err: RuntimeError) -> bool:
    return "404" in str(err) or "Not Found" in str(err)


def _denied(err: RuntimeError) -> bool:
    text = str(err)
    return "403" in text or "read:packages" in text or "scope" in text.lower()


def owner_packages(owner: str) -> tuple[str, list[dict]]:
    """(API base of the owner, its container packages): an org, or else a user."""
    for base in (f"/orgs/{owner}", f"/users/{owner}"):
        try:
            return base, core.api_pages(f"{base}/packages?package_type=container")
        except core.RateLimited:
            raise
        except RuntimeError as err:
            if not _missing(err):
                raise
    return f"/users/{owner}", []


def versions(base: str, name: str) -> list[dict]:
    """The newest versions of an image (each with its tags), newest first."""
    path = f"{base}/packages/container/{quote(name, safe='')}/versions?per_page=20"
    return api("GET", path) or []


def tags_of(version: dict) -> list[str]:
    return ((version.get("metadata") or {}).get("container") or {}).get("tags") or []


def released(node: dict) -> str | None:
    """The newest release tag of a repo's GraphQL node."""
    return newest([r["tagName"] for r in (node.get("releases") or {}).get("nodes") or []])


def package_infos(cfg: Config, repos: list[str], nodes: dict[str, dict] | None = None) -> tuple[list[dict], str]:
    """Every container image of the orgs whose repo bnb looks after (or that has no repo), with its newest
    versions, the repo's newest release, and the repo's recent image build runs. Returns (infos, error).
    One call per org, one per image, one per repo with images."""
    wanted = set(repos)
    owners = sorted({r.split("/")[0] for r in repos})
    try:
        found = core.pmap(owner_packages, owners)
    except core.RateLimited:
        raise
    except RuntimeError as err:
        return [], SCOPE_HINT if _denied(err) else str(err)
    infos = []
    for base, items in found:
        for p in items:
            repo = (p.get("repository") or {}).get("full_name")
            if repo and repo not in wanted:
                continue
            infos.append({"name": p["name"], "owner": base.rsplit("/", 1)[-1], "base": base, "repo": repo,
                          "url": p.get("html_url", ""), "versions": [], "release": None, "runs": [], "error": ""})
    if not infos:
        return [], ""
    linked = sorted({i["repo"] for i in infos if i["repo"]})
    nodes = nodes if nodes is not None else (R.nodes(linked) if linked else {})

    def load(info: dict) -> dict:
        try:
            info["versions"] = versions(info["base"], info["name"])
        except core.RateLimited:
            raise
        except RuntimeError as err:
            info["error"] = SCOPE_HINT if _denied(err) else str(err)
        return info

    def runs_of(repo: str) -> tuple[str, list[dict]]:
        try:
            found = (api("GET", f"/repos/{repo}/actions/runs?per_page=30") or {}).get("workflow_runs") or []
        except core.RateLimited:
            raise
        except RuntimeError:
            return repo, []
        return repo, [r for r in found if is_image_run(r)][:5]

    infos = core.pmap(load, infos)
    runs = dict(core.pmap(runs_of, linked))
    for i in infos:
        if i["repo"]:
            i["runs"] = runs.get(i["repo"], [])
            i["release"] = released(nodes.get(i["repo"]) or {})
    return sorted(infos, key=lambda i: (i["owner"], i["name"])), ""


def has_release_image(info: dict) -> bool:
    """Whether some version of the image is tagged with the repo's newest release (v1.2.3 or 1.2.3)."""
    want = (info["release"] or "").lstrip("v")
    return any(t.lstrip("v") == want for v in info["versions"] for t in tags_of(v))


def problems(info: dict) -> list[str]:
    out = []
    if info["error"]:
        out.append(f"error: {info['error']}")
    if info["release"] and info["versions"] and not has_release_image(info):
        out.append(f"no image tagged {info['release']}")
    last = info["runs"][0] if info["runs"] else None
    if last and last.get("conclusion") in ("failure", "timed_out", "startup_failure"):
        out.append(f"{last.get('name')} {R.run_state(last)}")
    return out


def version_label(info: dict) -> str:
    """The newest version-like tag, else the newest tag."""
    tags = [t for v in info["versions"] for t in tags_of(v)]
    versioned = [t for t in tags if t.lstrip("v")[:1].isdigit() and "." in t]
    return newest(versioned) or (tags[0] if tags else "-")


def row(info: dict) -> list[str]:
    top = info["versions"][0] if info["versions"] else {}
    last = info["runs"][0] if info["runs"] else None
    release = info["release"] or "-"
    if info["release"] and info["versions"]:
        release += " ✓" if has_release_image(info) else " missing"
    return [version_label(info), ago(top.get("updated_at") or top.get("created_at")) if top else "-", release,
            f"{R.run_state(last)} {ago(last.get('created_at'))}" if last else "-"]


def text(info: dict) -> str:
    """One image in detail: its newest versions and tags, the release check, image build runs."""
    lines = [f"{info['owner']}/{info['name']}", info["url"], f"repo     {info['repo'] or '-'}", ""]
    if info["error"]:
        lines += [f"error: {info['error']}", ""]
    if info["release"]:
        ok = has_release_image(info)
        lines += [f"release  {info['release']}: " + ("has its image" if ok else "NO IMAGE with this tag"), ""]
    lines.append("newest versions" if info["versions"] else "no versions")
    for v in info["versions"][:10]:
        tags = ", ".join(tags_of(v)) or "untagged"
        lines.append(f"  {ago(v.get('updated_at') or v.get('created_at')):<8} {tags}")
        lines.append(f"           {(v.get('name') or '')[:19]}")
    lines += ["", "image build runs" if info["runs"] else "no image build runs in the repo's last 30 runs"]
    for r in info["runs"]:
        lines.append(f"  {R.run_state(r):<10} {r.get('name')} · {r.get('head_branch')} · {ago(r.get('created_at'))}")
        lines.append(f"    {r.get('html_url', '')}")
    return "\n".join(lines)


def packages(cfg: Config, repos: list[str], only_problems: bool = False) -> None:
    infos, err = package_infos(cfg, repos)
    if err:
        say(err)
        return
    if only_problems:
        infos = [i for i in infos if problems(i)]
    if not infos:
        say("no problems" if only_problems else "no container images")
        return
    table(["image", "repo", "newest", "updated", "release", "image build"],
          [[f"{i['owner']}/{i['name']}", short(i["repo"]) if i["repo"] else "-", *row(i)] for i in infos])
    for i in infos:
        bad = problems(i)
        if not bad:
            continue
        say(f"\n== {i['owner']}/{i['name']}: " + "; ".join(bad))
        last = i["runs"][0] if i["runs"] else None
        if last and last.get("conclusion") == "failure":
            say(f"   {last.get('html_url', '')}")
            try:
                for line in R.failed_jobs(i["repo"], last["id"]):
                    say(f"   {line}")
            except core.RateLimited:
                raise
            except RuntimeError as e:
                say(f"   jobs: {e}")
        else:
            say(f"   {i['url']}")
