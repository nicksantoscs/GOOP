"""Terminal alternative to the in-app sign-in (Settings > Connect Garmin).

Reads GARMIN_EMAIL / GARMIN_PASSWORD from the environment, asks for the MFA code in *this terminal*
if Garmin wants one, then stores only the OAuth tokens (macOS Keychain via `keyring`, or
~/.goop/garmin_tokens/goop_tokens.json chmod 600). The password is never saved.
Restart the server afterwards (or just use the in-app sign-in, which needs no restart).

    export GARMIN_EMAIL=you@example.com
    read -s GARMIN_PASSWORD && export GARMIN_PASSWORD
    python setup_garmin.py
"""
import os
import sys

from garminconnect import Garmin

from goop import tokens

email, password = os.getenv("GARMIN_EMAIL"), os.getenv("GARMIN_PASSWORD")
if not (email and password):
    sys.exit("Set GARMIN_EMAIL and GARMIN_PASSWORD in your environment first.")

api = Garmin(email=email, password=password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
api.login()
where = tokens.save(api.client.dumps(), email=email, name=api.get_full_name())
print(f"Logged in as {api.get_full_name()}. Tokens saved to {'the keychain' if where == 'keychain' else tokens.FALLBACK_FILE}. "
      "You can unset GARMIN_PASSWORD now.")
