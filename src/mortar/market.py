"""The plugin marketplace: a signed index of bnb plugins, for `bnb market` and the operator.

An index is JSON:

    {"name": "mortar plugins", "description": "...", "generated": "2026-11-05",
     "plugins": [{"name": "hubra", "package": "hubra-cli", "version": "0.12.0",
                  "source": "git+https://github.com/hubra-app/hubra-cli@v0.12.0",
                  "description": "...", "homepage": "https://github.com/hubra-app/hubra-cli",
                  "provides": ["commands", "status", "doctor", "tabs", "checks"], "web": true}],
     "key_id": "...", "public_key": "<base64 Ed25519>", "signature": "<base64>"}

The signature covers name, description, generated, plugins, key_id and public_key (canonical
JSON). An index counts as verified only when it is signed by a key you trust: the keys in
mortar/keys/*.pub, BNB_PLUGIN_INDEX_KEYS (base64, comma separated) or --key. Nothing from an
unverified index is installed.

    bnb market                       list the plugins (and which are installed)
    bnb market install hubra         install one (pipx inject when bnb runs from pipx)
    bnb market keygen                a new signing key pair
    bnb market sign index.json --key private.pem
    bnb market verify index.json --key <public key>

Signing adapted from labdex (labdex/plugins/trust.py), relicensed into mortar (formerly bitsandbricks)
under MIT by its author.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

DEFAULT_INDEX = "https://raw.githubusercontent.com/bitsandbricks-ch/bitsandbricks-cli/main/plugins/index.json"
KEYS_DIR = Path(__file__).parent / "keys"
INDEX_FIELDS = ("name", "description", "generated", "plugins", "key_id", "public_key")
SOURCE = re.compile(r"^(git\+https://[\w.-]+/[\w./-]+(@[\w./-]+)?(#egg=[\w-]+)?|[A-Za-z0-9_.-]+==[\w.+-]+)$")
PACKAGE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _crypto():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
    except ImportError as err:  # pragma: no cover
        raise RuntimeError("signatures need the cryptography package: pipx inject mortar-cli cryptography") \
            from err
    return InvalidSignature, serialization, Ed25519PrivateKey, Ed25519PublicKey


def key_id(pub) -> str:
    _, serialization, _, _ = _crypto()
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def load_public(text: str):
    _, serialization, _, Ed25519PublicKey = _crypto()
    text = text.strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_public_key(text.encode())
    else:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(text))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not an Ed25519 public key")
    return key


def public_b64(pub) -> str:
    _, serialization, _, _ = _crypto()
    return base64.b64encode(pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def trusted_keys(extra: list[str] | None = None) -> dict:
    """{key_id: public key} of the bundled keys, BNB_PLUGIN_INDEX_KEYS and `extra`."""
    texts = []
    if KEYS_DIR.is_dir():
        texts += [f.read_text() for f in sorted(KEYS_DIR.glob("*.pub"))]
    texts += [k for k in (os.environ.get("BNB_PLUGIN_INDEX_KEYS") or "").split(",") if k.strip()]
    texts += [k for k in extra or [] if k.strip()]
    out = {}
    for t in texts:
        try:
            k = load_public(t)
        except (ValueError, TypeError):
            continue
        out[key_id(k)] = k
    return out


def _payload(doc: dict) -> bytes:
    return json.dumps({k: doc.get(k) for k in INDEX_FIELDS}, sort_keys=True, separators=(",", ":")).encode()


def keygen() -> tuple[bytes, str]:
    _, serialization, Ed25519PrivateKey, _ = _crypto()
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return pem, public_b64(key.public_key())


def sign(doc: dict, private_pem: bytes) -> dict:
    _, serialization, Ed25519PrivateKey, _ = _crypto()
    key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not an Ed25519 private key")
    pub = key.public_key()
    doc = {**doc, "key_id": key_id(pub), "public_key": public_b64(pub)}
    doc["signature"] = base64.b64encode(key.sign(_payload(doc))).decode()
    return doc


def verify(doc: dict, keys: dict) -> tuple[str, str]:
    """('verified', signer key id) or ('unverified', why)."""
    InvalidSignature, _, _, _ = _crypto()
    if not isinstance(doc, dict) or not doc.get("signature"):
        return "unverified", "the index isn't signed"
    kid = str(doc.get("key_id") or "")
    if kid not in keys:
        return "unverified", f"signed with key {kid or '?'}, which you don't trust (BNB_PLUGIN_INDEX_KEYS)"
    try:
        keys[kid].verify(base64.b64decode(doc["signature"]), _payload(doc))
    except (InvalidSignature, ValueError, TypeError):
        return "unverified", "bad signature: the index was changed after it was signed"
    return "verified", kid


def fetch(where: str | None = None, timeout: int = 15) -> dict:
    where = where or os.environ.get("BNB_PLUGIN_INDEX") or DEFAULT_INDEX
    if where.startswith("https://"):
        req = urllib.request.Request(where, headers={"User-Agent": "mortar", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https only
            raw = resp.read(2_000_000)
    elif where.startswith("http://"):
        raise RuntimeError("plugin indexes are fetched over https only")
    else:
        raw = Path(where).read_bytes()
    try:
        doc = json.loads(raw)
    except ValueError as err:
        raise RuntimeError(f"{where}: not JSON") from err
    if not isinstance(doc, dict) or not isinstance(doc.get("plugins"), list):
        raise RuntimeError(f"{where}: not a plugin index")
    return doc


def installed_version(package: str) -> str | None:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def entries(doc: dict) -> list[dict]:
    out = []
    for p in doc.get("plugins") or []:
        if not isinstance(p, dict) or not PACKAGE.match(str(p.get("package") or "")):
            continue
        source = str(p.get("source") or "")
        out.append({"name": str(p.get("name") or p["package"]), "package": p["package"],
                    "version": str(p.get("version") or ""), "source": source if SOURCE.match(source) else "",
                    "description": str(p.get("description") or ""), "homepage": str(p.get("homepage") or ""),
                    "provides": [str(x) for x in p.get("provides") or []], "web": bool(p.get("web")),
                    "license": str(p.get("license") or ""), "installed": installed_version(p["package"])})
    return out


def listing(where: str | None = None, keys: list[str] | None = None) -> dict:
    doc = fetch(where)
    state, detail = verify(doc, trusted_keys(keys))
    return {"name": doc.get("name", ""), "description": doc.get("description", ""), "generated": doc.get("generated"),
            "status": state, "detail": detail, "plugins": entries(doc)}


def install_command(source: str) -> list[str]:
    if not SOURCE.match(source):
        raise RuntimeError(f"won't install from {source!r}: only git+https://... or name==version")
    if "pipx" in sys.prefix.replace("\\", "/").split("/"):
        return ["pipx", "inject", "mortar-cli", source]
    return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", source]


def install(name: str, where: str | None = None, keys: list[str] | None = None) -> str:
    """Install a plugin of a verified index; returns pip's (or pipx's) output."""
    got = listing(where, keys)
    if got["status"] != "verified":
        raise RuntimeError(f"not installing from an unverified index: {got['detail']}")
    entry = next((p for p in got["plugins"] if p["name"] == name or p["package"] == name), None)
    if entry is None:
        raise RuntimeError(f"no plugin {name} in the index")
    if not entry["source"]:
        raise RuntimeError(f"{name} has no source bnb would install from")
    proc = subprocess.run(install_command(entry["source"]), capture_output=True, text=True, check=False, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(f"installing {name} failed:\n{(proc.stderr or proc.stdout)[-1500:]}")
    return (proc.stdout or "")[-1500:] + f"\ninstalled {name} {entry['version']}; restart bnb (or the operator)"


def command(action: str, name: str | None, where: str | None, key: str | None) -> None:
    keys = [k for k in (key or "").split(",") if k.strip()]
    if action == "keygen":
        pem, pub = keygen()
        path = Path("bnb-index.key")
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        print(f"private key: {path} (keep it secret)\npublic key:  {pub}\n"
              "Trust it with BNB_PLUGIN_INDEX_KEYS, or commit it as src/mortar/keys/<name>.pub")
        return
    if action == "sign":
        if not name or not key:
            raise RuntimeError("bnb market sign index.json --key private.pem")
        doc = json.loads(Path(name).read_text())
        doc.pop("signature", None)
        signed = sign(doc, Path(key).read_bytes())
        Path(name).write_text(json.dumps(signed, indent=2) + "\n")
        print(f"signed {name} with key {signed['key_id']}")
        return
    if action == "verify":
        doc = fetch(name or where)
        print(" ".join(verify(doc, trusted_keys(keys))))
        return
    if action == "install":
        if not name:
            raise RuntimeError("bnb market install <name>")
        print(install(name, where, keys))
        return
    got = listing(where, keys)
    print(f"{got['name'] or 'plugin index'}: {got['status']} ({got['detail']})\n")
    for p in got["plugins"]:
        mark = f"installed {p['installed']}" if p["installed"] else "-"
        print(f"{p['name']:<16} {p['version']:<10} {mark:<20} {p['description'][:70]}")
    if got["status"] != "verified":
        print("\nbnb only installs from a verified index.")
