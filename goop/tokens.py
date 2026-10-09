"""Garmin token storage. The password is NEVER stored; only garminconnect's OAuth tokens
(DI access + refresh token, valid ~1 year) plus the account's display name/email.

Primary store: the OS keychain via `keyring` (macOS Keychain on the Mac).
Fallback (keyring missing / no backend): ~/.goop/garmin_tokens/goop_tokens.json, chmod 600 in a 700 dir.
Cloud (GOOP_GARMIN_TOKENS set, e.g. on Render): tokens come from that base64-JSON secret (made by export_tokens.py).
  The secret is the source of truth; refreshed tokens are kept in memory only (no disk on Render free) and
  refresh_info() reports whether a refresh happened. Garmin tokens last about a year, so re-export before then.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("goop")

SERVICE, ACCOUNT = "goop-garmin", "oauth-tokens"
TOKEN_DIR = Path(os.getenv("GOOP_TOKEN_DIR", str(Path.home() / ".goop" / "garmin_tokens")))
FALLBACK_FILE = TOKEN_DIR / "goop_tokens.json"


ENV_VAR = "GOOP_GARMIN_TOKENS"
_MEM: dict = {"blob": None, "cleared": False, "refreshes": 0, "refreshed_at": None, "loaded_at": None}


def env_mode() -> bool:
    return bool(os.getenv(ENV_VAR)) or os.getenv("GOOP_CLOUD") == "1"


def _env_blob() -> dict | None:
    import base64
    raw = os.getenv(ENV_VAR, "").strip()
    if not raw:
        return None
    try:
        d = json.loads(base64.b64decode(raw + "=" * (-len(raw) % 4)).decode())
    except Exception:  # noqa: BLE001
        log.warning("%s is not valid base64 JSON; ignoring it", ENV_VAR)
        return None
    if isinstance(d, dict) and "garmin" not in d and "di_token" in d:  # raw client.dumps() also accepted
        d = {"garmin": json.dumps(d)}
    if not isinstance(d, dict) or not d.get("garmin"):
        log.warning("%s has no Garmin tokens; ignoring it", ENV_VAR)
        return None
    if not isinstance(d["garmin"], str):
        d["garmin"] = json.dumps(d["garmin"])
    return d


def refresh_info() -> dict:
    """Safe to expose (no token material): where tokens live and whether they were refreshed in memory."""
    return {"store": backend(), "from_env": bool(os.getenv(ENV_VAR)), "refreshed": _MEM["refreshes"] > 0,
            "refresh_count": _MEM["refreshes"], "refreshed_at": _MEM["refreshed_at"], "loaded_at": _MEM["loaded_at"],
            "note": ("Refreshed tokens live in memory only; the GOOP_GARMIN_TOKENS secret stays the source of truth "
                     "and keeps working until Garmin's refresh token expires (about a year).") if env_mode() else None}


def _keyring():
    if os.getenv("GOOP_NO_KEYRING") == "1" or env_mode():
        return None
    try:
        import keyring
        from keyring.backends import fail

        kr = keyring.get_keyring()
        if isinstance(kr, fail.Keyring) or "fail" in type(kr).__module__ or "null" in type(kr).__module__:
            return None
        return keyring
    except Exception:  # noqa: BLE001
        return None


def backend() -> str:
    return "env" if env_mode() else "keychain" if _keyring() else "file"


def _write_file(text: str) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TOKEN_DIR, 0o700)
    tmp = FALLBACK_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, FALLBACK_FILE)


def save(garmin_tokens_json: str, *, email: str | None = None, name: str | None = None) -> str:
    """Persist tokens; returns the backend used. Keeps previous email/name if not given."""
    prev = load() or {}
    d = {"garmin": garmin_tokens_json, "email": email or prev.get("email"),
         "name": name or prev.get("name"), "saved_at": time.time()}
    if env_mode():  # never touch disk in the cloud
        if prev.get("garmin") and prev["garmin"] != garmin_tokens_json:
            _MEM["refreshes"] += 1
            _MEM["refreshed_at"] = time.time()
            log.info("Garmin tokens changed (refresh/sign-in); kept in memory only")
        _MEM.update(blob=d, cleared=False)
        return "env"
    blob = json.dumps(d)
    kr = _keyring()
    if kr:
        try:
            kr.set_password(SERVICE, ACCOUNT, blob)
            if FALLBACK_FILE.exists():
                FALLBACK_FILE.unlink()
            return "keychain"
        except Exception as e:  # noqa: BLE001
            log.warning("Keychain write failed (%s); using file fallback", type(e).__name__)
    _write_file(blob)
    return "file"


def load() -> dict | None:
    """{'garmin': <tokens json str>, 'email', 'name', 'saved_at'} or None."""
    if env_mode():
        if _MEM["cleared"]:
            return None
        if _MEM["blob"] is None:
            _MEM["blob"] = _env_blob()
            if _MEM["blob"]:
                _MEM["loaded_at"] = time.time()
        return dict(_MEM["blob"]) if _MEM["blob"] else None
    kr = _keyring()
    if kr:
        try:
            v = kr.get_password(SERVICE, ACCOUNT)
            if v:
                return json.loads(v)
        except Exception as e:  # noqa: BLE001
            log.warning("Keychain read failed (%s)", type(e).__name__)
    if FALLBACK_FILE.exists():
        try:
            return json.loads(FALLBACK_FILE.read_text())
        except (OSError, ValueError):
            return None
    return None


def delete() -> None:
    if env_mode():  # disconnect for this process; the env secret comes back on the next restart
        _MEM.update(blob=None, cleared=True)
        return
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(SERVICE, ACCOUNT)
        except Exception:  # noqa: BLE001  (not present)
            pass
    if TOKEN_DIR.exists():  # fallback file + any legacy garminconnect token files
        for p in TOKEN_DIR.iterdir():
            if p.is_file():
                p.unlink()


def migrate_legacy() -> bool:
    """Move tokens written by older Goop / setup_garmin.py (garminconnect client.dump(dir))
    into the current store. Returns True if something was migrated."""
    if env_mode():
        return False
    if load() or not TOKEN_DIR.exists():
        return False
    legacy = [p for p in TOKEN_DIR.iterdir() if p.is_file() and p.name != FALLBACK_FILE.name]
    if not legacy:
        return False
    try:
        from garminconnect.client import Client

        c = Client()
        c.load(str(TOKEN_DIR))
        save(c.dumps())
    except Exception as e:  # noqa: BLE001
        log.warning("Could not migrate legacy Garmin tokens (%s)", type(e).__name__)
        return False
    for p in legacy:
        p.unlink()
    log.info("Migrated legacy Garmin tokens to %s", backend())
    return True
