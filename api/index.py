"""Vercel entrypoint: one Python serverless function running the whole Goop FastAPI app.
vercel.json rewrites every path here; FastAPI serves /api/*, the PWA shell and /static/*.
Serverless rules: only /tmp is writable, instances are short-lived, nothing persists between cold starts.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("GOOP_CLOUD", "1")       # access key on every /api route, Secure cookie
os.environ.setdefault("GOOP_SERVERLESS", "1")  # no in-app password sign-in, no disk writes
os.environ.setdefault("GOOP_NO_KEYRING", "1")
os.environ.setdefault("GOOP_TOKEN_DIR", "/tmp/goop_tokens")  # never used in env mode; /tmp is the only writable path
if not os.access(os.path.expanduser("~"), os.W_OK):
    os.environ["HOME"] = "/tmp"  # any library that touches ~ gets a writable place

from server import app  # noqa: E402,F401  (Vercel looks for `app`)
