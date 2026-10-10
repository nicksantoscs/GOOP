"""Garmin token storage. The password is NEVER stored; only garminconnect's OAuth tokens
(DI access + refresh token, valid ~1 year) plus the account's display name/email.

Primary store: the OS keychain via `keyring` (macOS Keychain on the Mac).
Fallback (keyring missing / no backend): ~/.goop/garmin_tokens/goop_tokens.json, chmod 600 in a 700 dir.
Cloud (GOOP_GARMIN_TOKENS set, e.g. on Render): tokens come from that base64-JSON secret (made by export_tokens.py).
  The secret is the source of truth; refreshed tokens are kept in memory only (no disk on Render free) and
  refresh_info() reports whether a refresh happened. Garmin tokens last about a year, so re-export before then.
  Optional DB (DATABASE_URL / POSTGRES_URL + GOOP_TOKEN_KEY Fernet key): refreshed tokens are encrypted and
  upserted into goop_tokens (row 'owner') so they survive restarts and Garmin rotating the refresh token.
  The DB is seeded from GOOP_GARMIN_TOKENS on first run; if it's missing/unreachable we fall back to env-only.
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
_MEM: dict = {"blob": None, "cleared": False, "refreshes": 0, "refreshed_at": None, "loaded_at": None,
              "source": None, "db_hash": None, "db_updated_at": None, "db_refresh_count": None, "db_error": None}
DB_ROW = "owner"


def _db_url() -> str | None:
    return (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip() or None


def _fernet():
    k = os.getenv("GOOP_TOKEN_KEY", "").strip()
    if not k:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(k.encode())
    except Exception as e:  # noqa: BLE001
        log.warning("GOOP_TOKEN_KEY unusable (%s); DB token store disabled", type(e).__name__)
        return None


def _hash(garmin: str) -> str:
    import hashlib
    return hashlib.sha256(garmin.encode()).hexdigest()


def _db_conn():
    """psycopg connection or None (logs a secret-free warning)."""
    url = _db_url()
    if not url or not env_mode():
        return None
    try:
        import psycopg
        c = psycopg.connect(url, connect_timeout=int(os.getenv("GOOP_DB_TIMEOUT", "4")), autocommit=True)
        c.execute("CREATE TABLE IF NOT EXISTS goop_tokens (id text PRIMARY KEY, ciphertext text NOT NULL, "
                  "updated_at timestamptz NOT NULL DEFAULT now(), refresh_count int NOT NULL DEFAULT 0)")
        return c
    except Exception as e:  # noqa: BLE001
        _MEM["db_error"] = type(e).__name__
        log.warning("Token DB unreachable (%s); using GOOP_GARMIN_TOKENS only", type(e).__name__)
        return None


def _db_read() -> dict | None:
    f = _fernet()
    if not f or not _db_url():
        return None
    c = _db_conn()
    if not c:
        return None
    try:
        with c:
            r = c.execute("SELECT ciphertext, updated_at, refresh_count FROM goop_tokens WHERE id=%s",
                          (DB_ROW,)).fetchone()
        if not r:
            return None
        d = json.loads(f.decrypt(r[0].encode()).decode())
        if not isinstance(d, dict) or not d.get("garmin"):
            return None
        _MEM.update(db_hash=_hash(d["garmin"]), db_updated_at=r[1].isoformat(), db_refresh_count=r[2], db_error=None)
        return d
    except Exception as e:  # noqa: BLE001
        _MEM["db_error"] = type(e).__name__
        log.warning("Token DB read failed (%s); using GOOP_GARMIN_TOKENS", type(e).__name__)
        return None


def _db_write(d: dict, *, refreshed: bool) -> bool:
    """Upsert encrypted tokens only if they changed since the last DB read/write."""
    f = _fernet()
    if not f or not _db_url() or not d.get("garmin"):
        return False
    h = _hash(d["garmin"])
    if h == _MEM["db_hash"]:
        return True
    c = _db_conn()
    if not c:
        return False
    try:
        ct = f.encrypt(json.dumps(d).encode()).decode()
        with c:
            r = c.execute("INSERT INTO goop_tokens (id, ciphertext, updated_at, refresh_count) VALUES (%s,%s,now(),0) "
                          "ON CONFLICT (id) DO UPDATE SET ciphertext=EXCLUDED.ciphertext, updated_at=now(), "
                          "refresh_count=goop_tokens.refresh_count + %s RETURNING updated_at, refresh_count",
                          (DB_ROW, ct, 1 if refreshed else 0)).fetchone()
        _MEM.update(db_hash=h, db_updated_at=r[0].isoformat(), db_refresh_count=r[1], db_error=None)
        log.info("Garmin tokens saved to DB (%s)", "refresh" if refreshed else "seed")
        return True
    except Exception as e:  # noqa: BLE001
        _MEM["db_error"] = type(e).__name__
        log.warning("Token DB write failed (%s); tokens kept in memory", type(e).__name__)
        return False


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
            "source": _MEM["source"], "db_configured": bool(_db_url() and os.getenv("GOOP_TOKEN_KEY")),
            "db_updated_at": _MEM["db_updated_at"], "db_refresh_count": _MEM["db_refresh_count"],
            "db_error": _MEM["db_error"],
            "note": (("Refreshed tokens are saved encrypted in the database." if _MEM["source"] == "db" or
                      _MEM["db_hash"] else "Refreshed tokens live in memory only; the GOOP_GARMIN_TOKENS secret is "
                      "the source of truth.")) if env_mode() else None}


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
            log.info("Garmin tokens changed (refresh/sign-in)")
        _MEM.update(blob=d, cleared=False)
        _db_write(d, refreshed=bool(prev.get("garmin") and prev["garmin"] != garmin_tokens_json))
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
            db = _db_read()
            if db:
                _MEM.update(blob=db, source="db")
            else:
                _MEM["blob"] = _env_blob()
                if _MEM["blob"]:
                    _MEM["source"] = "env"
                    _db_write(_MEM["blob"], refreshed=False)  # seed (no-op if DB/key missing)
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
