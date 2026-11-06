"""Keep repos on their defaults: `mason doctor` (check, --fix) and `mason sync` (files via PRs).

What a repo should have comes from its group in mason.toml (see config.py):
a live reference repo (`from`), explicit settings, a rulesets folder, files. On top
of that each repo's own `.github/rulesets/*.json` must match what's live.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.parse

from mason import core, plugins, workflows
from mason.config import Config, Group
from mason.core import WEB, api, compact, file_on, for_each, get_file, git_blob_sha, open_pr, say, short

REPO_SETTINGS = ("allow_auto_merge", "allow_merge_commit", "allow_rebase_merge", "allow_squash_merge",
                 "allow_update_branch", "delete_branch_on_merge", "has_discussions", "has_issues",
                 "has_projects", "has_wiki", "merge_commit_message", "merge_commit_title",
                 "squash_merge_commit_message", "squash_merge_commit_title", "web_commit_signoff_required")
PATCHABLE_SECURITY = ("advanced_security", "secret_scanning", "secret_scanning_push_protection",
                      "secret_scanning_non_provider_patterns", "secret_scanning_validity_checks")
RULESET_FIELDS = ("name", "target", "enforcement", "conditions", "rules", "bypass_actors")
ACTIONS = ("enabled", "allowed_actions")  # /actions/permissions
WORKFLOW = ("default_workflow_permissions", "can_approve_pull_request_reviews")  # /actions/permissions/workflow


# ── what a repo has ──────────────────────────────────────────────────────────


def flag(repo: str, endpoint: str) -> bool | None:
    """On/off endpoints (immutable-releases, private-vulnerability-reporting): None when unreadable."""
    try:
        return bool((api("GET", f"/repos/{repo}/{endpoint}") or {}).get("enabled"))
    except RuntimeError as err:
        return False if endpoint == "immutable-releases" and "404" in str(err) else None


def live_rulesets(repo: str) -> dict[str, dict]:
    listed = api("GET", f"/repos/{repo}/rulesets?includes_parents=false") or []
    return {r["name"]: api("GET", f"/repos/{repo}/rulesets/{r['id']}") or {} for r in listed}


def repo_state(repo: str) -> dict:
    info = api("GET", f"/repos/{repo}") or {}
    return {
        "default_branch": info.get("default_branch") or "main",
        "settings": {k: info.get(k) for k in REPO_SETTINGS if k in info},
        "security": {k: (v or {}).get("status") for k, v in (info.get("security_and_analysis") or {}).items()},
        "vulnerability_reporting": flag(repo, "private-vulnerability-reporting"),
        "immutable_releases": flag(repo, "immutable-releases"),
        "rulesets": live_rulesets(repo),
        "info": {"topics": info.get("topics") or [], "description": info.get("description") or "",
                 "visibility": info.get("visibility") or ("private" if info.get("private") else "public")},
    }


def alerts_on(repo: str) -> str | None:
    """Dependabot alerts: "enabled" (204), "disabled" (404), None when unreadable."""
    try:
        api("GET", f"/repos/{repo}/vulnerability-alerts")
        return "enabled"
    except RuntimeError as err:
        return "disabled" if "404" in str(err) else None


def ruleset_files(repo: str, ref: str | None = None) -> dict[str, tuple[str, dict]]:
    """The repo's own .github/rulesets/*.json, by ruleset name: {name: (file, ruleset)}."""
    q = f"?ref={ref}" if ref else ""
    try:
        listing = api("GET", f"/repos/{repo}/contents/.github/rulesets{q}") or []
    except RuntimeError:
        return {}
    out = {}
    for f in sorted(x["name"] for x in listing if isinstance(x, dict) and x.get("name", "").endswith(".json")):
        text = file_on(repo, f".github/rulesets/{f}", ref)
        try:
            rs = json.loads(text) if text else None
        except ValueError:
            rs = {"name": f, "_broken": True}
        if rs:
            out[rs.get("name") or f] = (f, rs)
    return out


def blob_shas(repo: str, ref: str = "HEAD") -> dict[str, str]:
    """{path: blob sha} of every file on `ref`, in one call."""
    t = api("GET", f"/repos/{repo}/git/trees/{ref}?recursive=1") or {}
    return {e["path"]: e["sha"] for e in t.get("tree") or [] if e.get("type") == "blob"}


# ── what it should have ──────────────────────────────────────────────────────


def baseline(cfg: Config, group: Group) -> dict:
    """A group's wanted state: the reference repo's live state, then the explicit values on top."""
    want: dict = {"settings": {}, "security": {}, "vulnerability_reporting": None, "immutable_releases": None,
                  "rulesets": {}, "files": {}, "texts": {}, "label": group.origin or "defaults",
                  "reference": group.reference,
                  "group": group.name}
    if group.reference:
        ref = repo_state(group.reference)
        want.update({k: ref[k] for k in ("settings", "security", "vulnerability_reporting",
                                         "immutable_releases", "rulesets")})
        want["label"] = short(group.reference)
        want["ref_settings"] = dict(ref["settings"])
        want["ref_files"] = ruleset_files(group.reference)
        want["ref_branch"] = ref["default_branch"]
        if isinstance(group.files, list):
            shas = blob_shas(group.reference, ref["default_branch"])
            missing = [p for p in group.files if p not in shas]
            if missing:
                want["missing_files"] = missing
            want["files"] = {p: shas[p] for p in group.files if p in shas}
            want["head"] = (api("GET", f"/repos/{group.reference}/git/ref/heads/{ref['default_branch']}")
                            or {}).get("object", {}).get("sha", "")
    want["settings"].update(group.require)  # the reference must have these, so its repos must too
    want["settings"].update(group.settings)
    want["security"].update(group.security)
    for key in ("vulnerability_reporting", "immutable_releases"):
        if getattr(group, key) is not None:
            want[key] = getattr(group, key)
    want["rulesets"].update(cfg.rulesets_of(group))
    texts = cfg.files_of(group)
    want["files"].update({p: git_blob_sha(t) for p, t in texts.items()})
    want["texts"].update(texts)
    want.update(workflows=workflows.settings(group.workflows), topics=list(group.topics),
                labels=dict(group.labels), actions=dict(group.actions),
                required_files=list(group.required_files), branch=group.branch, visibility=group.visibility,
                description=group.description, skip=set(group.skip))
    return skip(want)


def skip(want: dict) -> dict:
    """Leave out what the group's `skip` names."""
    off = want["skip"]
    for key in ("settings", "security", "rulesets", "files", "labels", "actions"):
        if key in off:
            want[key] = {}
    for key in ("topics", "required_files"):
        if key in off:
            want[key] = []
    for key in ("vulnerability_reporting", "immutable_releases", "branch", "visibility"):
        if key in off:
            want[key] = None
    if "description" in off:
        want["description"] = False
    if "workflows" in off:
        want["workflows"] = {}
    return want


def file_texts(want: dict) -> dict[str, str]:
    """Texts of every wanted file (the reference's are downloaded on first use)."""
    for path in want["files"]:
        if path not in want["texts"] and want.get("reference"):
            got = get_file(want["reference"], path, want.get("ref_branch"))
            if got:
                want["texts"][path] = got[0]
    return want["texts"]


# ── comparing ────────────────────────────────────────────────────────────────


def covers(have, want) -> bool:
    """True when `have` has every key and value of `want` (ruleset files leave out GitHub's defaults)."""
    if isinstance(want, dict) and isinstance(have, dict):
        return all(k in have and covers(have[k], v) for k, v in want.items())
    return have == want


def bypass(rs: dict) -> list:
    return sorted((a.get("actor_type"), a.get("actor_id"), a.get("bypass_mode")) for a in rs.get("bypass_actors") or [])


def ruleset_drift(have: dict, want: dict, against: str = "defaults", subset: bool = False) -> list[str]:
    same = covers if subset else (lambda a, b: a == b)
    out = []
    for key in ("target", "enforcement"):
        if key in want and have.get(key) != want[key]:
            out.append(f"{key} is {have.get(key)}, {against} has {want[key]}")
    if ("conditions" in want or not subset) and not same(have.get("conditions") or {}, want.get("conditions") or {}):
        out.append(f"conditions {compact(have.get('conditions'))}, {against} has {compact(want.get('conditions'))}")
    hr = {r["type"]: r.get("parameters") or {} for r in have.get("rules") or []}
    wr = {r["type"]: r.get("parameters") or {} for r in want.get("rules") or []}
    if set(wr) - set(hr):
        out.append(f"lacks {', '.join(sorted(set(wr) - set(hr)))}")
    if set(hr) - set(wr):
        out.append(f"has {', '.join(sorted(set(hr) - set(wr)))}, {against} doesn't")
    for t in sorted(set(hr) & set(wr)):
        for k in sorted(set(wr[t]) | (set() if subset else set(hr[t]))):
            if not same(hr[t].get(k), wr[t].get(k)):
                out.append(f"{t}.{k} is {compact(hr[t].get(k))}, {against} has {compact(wr[t].get(k))}")
    if "bypass_actors" in want and bypass(have) != bypass(want):
        def who(rs):
            return ", ".join(f"{t} {i} ({m})" for t, i, m in bypass(rs)) or "nobody"
        out.append(f"bypass is {who(have)}, {against} has {who(want)}")
    return out


def state_drift(have: dict, want: dict, own: dict | None = None) -> list[str]:
    """Settings, security, release immutability and rulesets that differ from what the group wants."""
    label = want["label"]
    problems = []
    for key, value in want["settings"].items():
        if have["settings"].get(key) != value:
            problems.append(f"{key} is {compact(have['settings'].get(key))}, {label} has {compact(value)}")
    for key, value in want["security"].items():
        if have["security"].get(key) != value:
            problems.append(f"{key} is {have['security'].get(key) or 'off'}, {label} has {value}")
    for key, name in (("vulnerability_reporting", "private vulnerability reporting"),
                      ("immutable_releases", "release immutability")):
        if want[key] is None:
            continue
        if have[key] is None:
            problems.append(f"{name}: couldn't read it")
        elif have[key] != want[key]:
            problems.append(f"{name} is {'on' if have[key] else 'off'}, {label} has {'on' if want[key] else 'off'}")
    for name, rs in want["rulesets"].items():
        if name not in have["rulesets"]:
            problems.append(f"ruleset {name} is missing")
        else:
            # rulesets from the defaults folder may leave out GitHub's defaults; a live reference has them all
            subset = name not in (want.get("live_names") or ())
            problems += [f"ruleset {name}: {d}"
                         for d in ruleset_drift(have["rulesets"][name], rs, label, subset=subset)]
    if want["rulesets"]:
        for name in sorted(set(have["rulesets"]) - set(want["rulesets"]) - set(own or {})):
            problems.append(f"ruleset {name} is neither in {label} nor in .github/rulesets")
    return problems


def files_drift(live: dict[str, dict], own: dict[str, tuple[str, dict]]) -> list[str]:
    """The repo's live rulesets against its own .github/rulesets files."""
    problems = []
    for name, (f, rs) in own.items():
        if rs.get("_broken"):
            problems.append(f".github/rulesets/{f} isn't valid JSON")
        elif name not in live:
            problems.append(f"ruleset {name} (.github/rulesets/{f}) isn't imported")
        else:
            problems += [f"ruleset {name}: {d}" for d in ruleset_drift(live[name], rs, against=f, subset=True)]
    return problems


def file_drift(repo: str, want: dict, branch: str) -> list[str]:
    if not want["files"]:
        return []
    ours = blob_shas(repo, branch)
    out = []
    for path, sha in want["files"].items():
        if path not in ours:
            out.append(f"{path} is missing")
        elif ours[path] != sha:
            out.append(f"{path} differs from {want['label']}")
    return out


def required_drift(repo: str, want: dict, branch: str) -> list[str]:
    if not want.get("required_files"):
        return []
    ours = blob_shas(repo, branch)
    return [f"{need.replace('|', ' or ')} is missing" for need in want["required_files"]
            if not any(p.strip() in ours for p in need.split("|"))]


def repo_drift(repo: str, state: dict, want: dict) -> tuple[list[str], list[str]]:
    """Topics, labels and Actions permissions (doctor --fix sets them), then what's only checked:
    the default branch, visibility and description."""
    info, label = state["info"], want["label"]
    fix: list[str] = []
    missing = [t for t in want.get("topics") or [] if t not in info["topics"]]
    if missing:
        fix.append(f"topics lack {', '.join(missing)}")
    if want.get("labels"):
        have = {x["name"].lower(): x for x in core.api_pages(f"/repos/{repo}/labels")}
        for name, spec in want["labels"].items():
            got = have.get(name.lower())
            if not got:
                fix.append(f"label {name} is missing")
            elif spec.get("color") and got.get("color", "").lower() != spec["color"]:
                fix.append(f"label {name} is #{got.get('color')}, {label} has #{spec['color']}")
    for key, value in actions_drift(repo, want.get("actions") or {}).items():
        fix.append(f"actions {key} is {compact(value[0])}, {label} has {compact(value[1])}")
    check = []
    if want.get("branch") and state["default_branch"] != want["branch"]:
        check.append(f"default branch is {state['default_branch']}, {label} has {want['branch']}")
    if want.get("visibility") and info["visibility"] != want["visibility"]:
        check.append(f"the repo is {info['visibility']}, {label} has {want['visibility']}")
    if want.get("description") and not info["description"].strip():
        check.append("it has no description")
    return fix, check


def actions_drift(repo: str, want: dict) -> dict[str, tuple]:
    """{key: (have, want)} of the Actions permissions that differ."""
    out = {}
    for keys, path in ((ACTIONS, "actions/permissions"), (WORKFLOW, "actions/permissions/workflow")):
        asked = {k: want[k] for k in keys if k in want}
        if asked:
            have = api("GET", f"/repos/{repo}/{path}") or {}
            out.update({k: (have.get(k), v) for k, v in asked.items() if have.get(k) != v})
    return out


def fix_repo(repo: str, state: dict, want: dict) -> None:
    """Add the missing topics and labels, fix label colors, set the Actions permissions."""
    topics = state["info"]["topics"]
    if any(t not in topics for t in want.get("topics") or []):
        api("PUT", f"/repos/{repo}/topics", {"names": list(dict.fromkeys(topics + want["topics"]))})
    if want.get("labels"):
        have = {x["name"].lower(): x for x in core.api_pages(f"/repos/{repo}/labels")}
        for name, spec in want["labels"].items():
            got = have.get(name.lower())
            body = {k: spec[k] for k in ("color", "description") if spec.get(k)}
            if not got:
                api("POST", f"/repos/{repo}/labels", {"name": name, "color": spec.get("color") or "ededed", **body})
            elif spec.get("color") and got.get("color", "").lower() != spec["color"]:
                api("PATCH", f"/repos/{repo}/labels/{urllib.parse.quote(got['name'])}", body)
    acts = want.get("actions") or {}
    drift = actions_drift(repo, acts)
    if any(k in drift for k in ACTIONS):
        api("PUT", f"/repos/{repo}/actions/permissions",
            {"enabled": acts.get("enabled", True), **{k: acts[k] for k in ACTIONS if k in acts}})
    if any(k in drift for k in WORKFLOW):
        have = api("GET", f"/repos/{repo}/actions/permissions/workflow") or {}
        api("PUT", f"/repos/{repo}/actions/permissions/workflow",
            {**{k: have[k] for k in WORKFLOW if k in have}, **{k: acts[k] for k in WORKFLOW if k in acts}})


# ── fixing ───────────────────────────────────────────────────────────────────


def import_rulesets(repo: str, rulesets: dict[str, dict], current: dict[str, int] | None = None) -> None:
    """Create or replace (by name) each ruleset."""
    if current is None:
        current = {r["name"]: r["id"] for r in api("GET", f"/repos/{repo}/rulesets?includes_parents=false") or []}
    for name, rs in rulesets.items():
        body = {k: rs[k] for k in RULESET_FIELDS if k in rs}
        body.setdefault("name", name)
        if name in current:
            api("PUT", f"/repos/{repo}/rulesets/{current[name]}", body)
        else:
            api("POST", f"/repos/{repo}/rulesets", body)


def apply(repo: str, want: dict, own: dict | None = None) -> None:
    """Settings, security, vulnerability reporting, release immutability and rulesets as the group wants,
    then the repo's own .github/rulesets files."""
    body: dict = dict(want["settings"])
    sec = {k: {"status": v} for k, v in want["security"].items() if k in PATCHABLE_SECURITY}
    if sec:
        body["security_and_analysis"] = sec
    if body:
        api("PATCH", f"/repos/{repo}", body)
    if "dependabot_alerts" in want["security"]:
        on = want["security"]["dependabot_alerts"] == "enabled"
        api("PUT" if on else "DELETE", f"/repos/{repo}/vulnerability-alerts")
    if "dependabot_security_updates" in want["security"]:
        on = want["security"]["dependabot_security_updates"] == "enabled"
        api("PUT" if on else "DELETE", f"/repos/{repo}/automated-security-fixes")
    if want["vulnerability_reporting"] is not None:
        api("PUT" if want["vulnerability_reporting"] else "DELETE", f"/repos/{repo}/private-vulnerability-reporting")
    if want["immutable_releases"] is not None:
        api("PUT" if want["immutable_releases"] else "DELETE", f"/repos/{repo}/immutable-releases")
    try:
        current = {r["name"]: r["id"] for r in api("GET", f"/repos/{repo}/rulesets?includes_parents=false") or []}
    except RuntimeError:
        current = {}
    import_rulesets(repo, want["rulesets"], current)
    custom = {n: rs for n, (_, rs) in (own or {}).items() if n not in want["rulesets"] and not rs.get("_broken")}
    import_rulesets(repo, custom, current)
    said = [f"{len(want['settings'])} settings"] if want["settings"] else []
    if want["rulesets"]:
        said.append(f"rulesets {', '.join(want['rulesets'])}")
    if custom:
        said.append(f"own rulesets {', '.join(custom)}")
    say(f"   applied {', '.join(said) or 'security settings'} from {want['label']}")


# ── doctor ───────────────────────────────────────────────────────────────────


def doctor_ctx(cfg: Config, repos: list[str], fix: bool = False) -> dict:
    from mason.repos import group_of

    groups = {r: group_of(cfg, r) for r in repos}
    used = {g.name: g for g in groups.values() if g}
    refs = {g.name: g for g in cfg.groups_for(repos) if g.reference and (g.name in used or g.reference in repos)}
    wants = {}
    for g in {**used, **refs}.values():
        wants[g.name] = baseline(cfg, g)
        wants[g.name]["live_names"] = set(wants[g.name]["rulesets"]) - set(cfg.rulesets_of(g))
    return {"cfg": cfg, "groups": groups, "wants": wants, "refs": list(refs.values()), "fix": fix}


def reference_check(group: Group, want: dict, fix: bool) -> None:
    """The reference repo against its own ruleset files and the group's `require`."""
    ref = group.reference
    problems = files_drift(want["rulesets"], want.get("ref_files") or {})
    live = want.get("ref_settings") or {}
    req = [f"{k} is {compact(live.get(k))}, should be {compact(v)}"
           for k, v in group.require.items() if live.get(k) != v]
    if want.get("missing_files"):
        problems.append(f"files the group syncs aren't there: {', '.join(want['missing_files'])}")
    if fix and (problems or req):
        own = {n: rs for n, (_, rs) in (want.get("ref_files") or {}).items() if not rs.get("_broken")}
        if own:
            import_rulesets(ref, own)
            say(f"   rulesets ({', '.join(own)}) imported from .github/rulesets")
            if not core.dry_run():
                want["rulesets"] = live_rulesets(ref)
        if group.require:
            api("PATCH", f"/repos/{ref}", group.require)
            live.update(group.require)
            say(f"   set {', '.join(group.require)}")
        problems = [p for p in problems if p.startswith("files the group")]
        req = []
    for p in problems + req:
        say(f"   - {p}")
    if not problems and not req:
        say("   ok")
    elif not fix:
        say(f"   fix: mason doctor --fix {ref}  (the reference first, then its repos follow it)")
    rel = {True: "on", False: "off", None: "?"}[want["immutable_releases"]]
    say(f"   reference of group {group.name}: {len(want['settings'])} settings, {len(want['rulesets'])} rulesets, "
        f"{len(want['files'])} files, release immutability {rel}")


def doctor_all(cfg: Config, repos: list[str], fix: bool = False) -> None:
    ctx = doctor_ctx(cfg, repos, fix)
    for g in ctx["refs"]:
        say(f"== {g.reference} (the reference)")
        reference_check(g, ctx["wants"][g.name], fix)
    references = {g.reference for g in ctx["refs"]}
    for_each([r for r in repos if r not in references], doctor, ctx)


def doctor(repo: str, ctx: dict) -> None:
    group = ctx["groups"].get(repo)
    want = ctx["wants"].get(group.name) if group else None
    own = ruleset_files(repo)
    checked: list[str] = []
    repo_fix: list[str] = []
    if want:
        state = repo_state(repo)
        if "dependabot_alerts" in want["security"]:
            state["security"]["dependabot_alerts"] = alerts_on(repo)
        fixable = state_drift(state, want, own)
        missing = {n for n in want["rulesets"] if n not in state["rulesets"]}
        if "own_rulesets" not in want["skip"]:
            fixable += [p for p in files_drift(state["rulesets"], own)
                        if p not in fixable and not any(p.startswith(f"ruleset {n} (") for n in missing)]
        repo_fix, checked = repo_drift(repo, state, want)
        fixable += repo_fix
        branch = state["default_branch"]
    else:
        live = live_rulesets(repo)
        fixable = files_drift(live, own)
        branch = (api("GET", f"/repos/{repo}") or {}).get("default_branch") or "main"
    problems = list(fixable)
    if want:
        problems += file_drift(repo, want, branch) + required_drift(repo, want, branch) + checked
        if want.get("workflows"):
            problems += [f"workflow {f['path'].rsplit('/', 1)[-1]}:{f['line']} {f['text']}"
                         for f in workflows.findings(repo, want["workflows"])]
    if not (want and "plugins" in want["skip"]):
        problems += plugins.hook("doctor", repo, group, ctx)

    if fixable and ctx.get("fix"):
        if want:
            if [p for p in fixable if p not in repo_fix]:
                apply(repo, want, own)
            if repo_fix:
                fix_repo(repo, state, want)
                say(f"   set {len(repo_fix)} topics, labels or Actions permissions")
        else:
            import_rulesets(repo, {n: rs for n, (_, rs) in own.items() if not rs.get("_broken")})
            say(f"   rulesets ({', '.join(own)}) imported from .github/rulesets")
        problems = [p for p in problems if p not in fixable]
        say(f"   fixed {len(fixable)}")

    where = f" (group {group.name})" if group else " (no group: only its own .github/rulesets)"
    if not problems:
        say(f"   ok{where}")
        return
    say(f"  {where.strip()}")
    for p in problems:
        say(f"   - {p}")
    if any(p in fixable for p in problems):
        say(f"   fix: mason doctor --fix {repo}")
    if want and any(p.endswith(("is missing", f"differs from {want['label']}")) and not p.startswith("ruleset")
                    for p in problems):
        say(f"   fix: mason sync {repo}  (PR with the files)")
    if any(p.startswith("workflow ") and "isn't pinned" in p for p in problems):
        say(f"   fix: mason workflows --fix {repo}  (PR that pins the actions)")
    for hint in plugins.hook("doctor_hints", repo, group, problems):
        say(f"   fix: {hint}")


# ── sync files ───────────────────────────────────────────────────────────────


def sync_ctx(cfg: Config, repos: list[str], only: list[str] | None = None) -> dict:
    ctx = doctor_ctx(cfg, repos)
    for want in ctx["wants"].values():
        if only:
            unknown = [p for p in only if p not in want["files"]]
            want["files"] = {p: s for p, s in want["files"].items() if p in only}
            want["unknown"] = unknown
        file_texts(want)
    return ctx


def sync(repo: str, ctx: dict) -> None:
    group = ctx["groups"].get(repo)
    want = ctx["wants"].get(group.name) if group else None
    if not want or not want["files"]:
        say("   no files to sync" + ("" if group else " (no group)"))
        return
    if group.reference == repo:
        say("   the reference itself")
        return
    info = api("GET", f"/repos/{repo}") or {}
    branch_base = info.get("default_branch") or "main"
    ours = blob_shas(repo, branch_base)
    changed = {p: (want["texts"][p], ours.get(p)) for p, sha in want["files"].items()
               if ours.get(p) != sha and p in want["texts"]}
    if not changed:
        say(f"   in sync with {want['label']}")
        return
    for path, (_, sha) in changed.items():
        say(f"   {'update' if sha else 'add'} {path}")
    stamp = want.get("head") or hashlib.sha1("".join(sorted(want["files"].values())).encode()).hexdigest()
    branch = f"mason/sync-{stamp[:7]}"
    owner = repo.split("/")[0]
    if api("GET", f"/repos/{repo}/pulls?state=open&head={owner}:{branch}"):
        say(f"   PR already open: {WEB}/{repo}/pulls?q=head:{branch}")
        return
    if core.dry_run():
        say(f"   [dry-run] PR on {branch} with {len(changed)} file(s)")
        return
    lines = "\n".join(f"- `{p}`" for p in changed)
    group = ctx["groups"].get(repo)
    where = group.origin if group and group.origin else ctx["cfg"].source
    source = f"{want['reference']}@{stamp[:7]}" if want.get("reference") else f"the defaults ({where})"
    url = open_pr(repo, branch_base, branch, changed,
                  f"chore: sync shared files with {want['label']}",
                  f"Sync shared files with {want['label']}",
                  f"Copies these files from {source}:\n\n{lines}\n\n"
                  "Check the diff for changes this repo made on purpose before merging.\n\nOpened by `mason sync`.")
    say(f"   PR: {url}")


# ── new repos ────────────────────────────────────────────────────────────────


def create_repo(cfg: Config, repo: str, group_name: str | None = None, public: bool = False,
                description: str = "") -> None:
    """Create a repo the way its group wants it: from the reference repo when that is a GitHub
    template, the group's files committed, then its settings, security and rulesets applied."""
    if "/" not in repo:
        raise RuntimeError(f"Name the owner too: {repo} -> <owner>/{repo}")
    if core.exists(repo):
        raise RuntimeError(f"{repo} exists already (mason doctor --fix {repo} applies the defaults)")
    if group_name:
        group = cfg.find_group(group_name, repo)
        if not group:
            names = ", ".join(g.name for g in cfg.groups_for([repo])) or "none"
            raise RuntimeError(f"No group {group_name!r}. Groups: {names}")
    else:
        group = cfg.group_of(repo)
    want = baseline(cfg, group) if group else None
    template = bool(group and group.reference and (api("GET", f"/repos/{group.reference}") or {}).get("is_template"))
    say(f"== {repo}" + (f" (group {group.name})" if group else " (no group: a plain repo)"))
    if core.dry_run():
        how = f"from the template {group.reference}" if template else "with a README"
        say(f"   [dry-run] create {'public' if public else 'private'} {repo} {how}")
    else:
        core.transport().repo_create(repo, public, group.reference if template else None, description)
        say(f"   created {WEB}/{repo}" + (f" from the template {group.reference}" if template else ""))
        time.sleep(5 if template else 2)  # GitHub fills a new repo in the background
    if not want:
        return
    texts = file_texts(want)
    files = {p: t for p, t in texts.items() if p in want["files"]}
    own = cfg.files_of(group) if template else files  # a template brings the reference's files itself
    if own:
        for path, text in own.items():
            body = {"message": f"chore: add {path} from the defaults",
                    "content": base64.b64encode(text.encode()).decode()}
            api("PUT", f"/repos/{repo}/contents/{path}", body)
        say(f"   committed {len(own)} file(s): {', '.join(own)}")
    apply(repo, want)
    say(f"   check it: mason doctor {repo}")
