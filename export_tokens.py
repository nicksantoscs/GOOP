"""Print your saved Garmin tokens as one base64 string for a cloud host (e.g. Render's GOOP_GARMIN_TOKENS secret).

Run on the Mac where Goop is already signed in (tokens come from the macOS Keychain / Goop's token file):

    .venv/bin/python export_tokens.py

Treat the output like a password: it grants access to your Garmin data for about a year.
Paste it only into Render's dashboard (Environment > Secret), never into chat, git, or a file you commit.
The password is not included: only Garmin's OAuth tokens plus your display name/email.
"""
import base64
import json
import sys

from goop import tokens

if tokens.env_mode():
    sys.exit("Unset GOOP_GARMIN_TOKENS / GOOP_CLOUD first: this exports the Mac's local tokens.")
t = tokens.load()
if not t or not t.get("garmin"):
    sys.exit("No saved Garmin tokens. Connect Garmin in Goop (More > Connect Garmin) or run setup_garmin.py first.")
blob = {"garmin": t["garmin"], "email": t.get("email"), "name": t.get("name")}
print(base64.b64encode(json.dumps(blob, separators=(",", ":")).encode()).decode())
print(f"\n^ GOOP_GARMIN_TOKENS for {t.get('name') or t.get('email') or 'your account'} "
      f"(from {tokens.backend()}). Keep it secret.", file=sys.stderr)
