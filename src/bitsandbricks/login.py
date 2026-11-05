"""`bnb login <operator URL>`: sign the CLI and the TUI in to an operator with an API key.

The operator shows a code page; you approve the code in the browser where you are signed in with
GitHub or GitLab, and bnb gets an API key (read or write) that it keeps in
~/.config/bitsandbricks/operator.json (readable only by you). From then on every bnb command and
`bnb tui` go through the operator: no gh and no GitHub token needed on this machine.
`bnb logout` forgets the key (revoke it in the operator under Settings › API keys).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import webbrowser

from bitsandbricks import core, transport


def _post(url: str, data: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(data).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "bitsandbricks-cli")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310 - the URL the user typed
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except ValueError:
            return err.code, {}
    except urllib.error.URLError as err:
        raise RuntimeError(f"can't reach {url}: {err.reason}") from err


def login(url: str, scope: str = "write", key: str | None = None) -> None:
    url = url.rstrip("/")
    if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise RuntimeError("the operator URL must start with https:// (http only for localhost)")
    if key:
        t = transport.OperatorTransport(url, key)
        me = t.get_json("/me") or {}
        if not me.get("user"):
            raise RuntimeError("that API key isn't valid on this operator")
        path = transport.save_operator_login(url, key)
        print(f"Signed in to {url} as {me['user']['name']} ({me.get('scope')}); key saved in {path}")
        return
    status, got = _post(f"{url}/api/v1/device/code", {"scope": scope, "client": "bnb CLI"})
    if status != 200:
        raise RuntimeError(f"the operator refused: {got.get('error') or status}")
    link = got["verification_uri_complete"]
    print(f"Open {got['verification_uri']} and approve the code  {got['user_code']}")
    if core.open_mode() == "browser":
        webbrowser.open(link)
    else:
        print(f"  {link}")
    interval = int(got.get("interval") or 5)
    deadline = time.monotonic() + int(got.get("expires_in") or 600)
    while time.monotonic() < deadline:
        time.sleep(interval)
        status, ans = _post(f"{url}/api/v1/device/token", {"device_code": got["device_code"]})
        if status == 200 and ans.get("key"):
            path = transport.save_operator_login(url, ans["key"])
            print(f"Signed in to {url} as {ans.get('user') or '?'} ({ans.get('scope')} key); saved in {path}")
            print("bnb and bnb tui now go through the operator. bnb logout undoes it.")
            return
        err = ans.get("error")
        if err == "slow_down":
            interval += 2
        elif err == "access_denied":
            raise RuntimeError("the login was denied in the browser")
        elif err == "expired_token":
            break
    raise RuntimeError("the code expired before it was approved; run bnb login again")


def logout() -> None:
    url, _ = transport.operator_login()
    if transport.forget_operator_login():
        print(f"Forgot the API key for {url}. Revoke it in the operator too: Settings › API keys.")
    else:
        print("Not signed in to an operator.")


def whoami() -> None:
    t = transport.current()
    if isinstance(t, transport.OperatorTransport):
        me = t.get_json("/me") or {}
        user = me.get("user") or {}
        logins = ", ".join(f"{i['provider']}:{i['login']}" for i in me.get("identities") or [])
        print(f"operator  {t.operator}\nuser      {user.get('name', '?')} ({logins})\nkey       {me.get('scope')}")
    elif isinstance(t, transport.GhTransport):
        print("gh CLI (gh auth status shows the account); bnb login <operator URL> signs in to an operator")
    else:
        print(f"{getattr(t, 'name', '?')} transport")
