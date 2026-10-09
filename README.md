# Goop: your Garmin data without the clutter

A Whoop-style dashboard for Garmin Connect data. It has a Python (FastAPI) backend that uses the open-source `garminconnect` library, and an installable PWA frontend.

## Run
    ./run.sh                      # http://<this-computer>:8787  (binds 0.0.0.0)
If there are no Garmin tokens and no GARMIN_EMAIL/GARMIN_PASSWORD, Goop runs in **DEMO MODE** with labelled sample data.
Set `GOOP_DEMO=1` to force demo mode. `GOOP_PORT` changes the port.

## Connect Garmin (one time)
    export GARMIN_EMAIL=you@example.com
    read -s GARMIN_PASSWORD && export GARMIN_PASSWORD   # not saved in shell history
    .venv/bin/python setup_garmin.py                     # enter the MFA code here if Garmin asks
    unset GARMIN_PASSWORD; ./run.sh
Tokens are saved to `~/.goop/garmin_tokens` (mode 700), so later restarts don't need your password.

## Install on your phone
1. Run Goop on a computer on the same Wi-Fi as your phone and find its LAN IP (e.g. 192.168.1.20).
2. On the phone, open `http://192.168.1.20:8787`.
   * iPhone (Safari): Share, then Add to Home Screen. It opens full-screen like an app.
   * Android (Chrome): menu, then Add to Home screen.
   Over plain-HTTP LAN the offline service worker is disabled (browsers only allow it on HTTPS),
   but the app works fully whenever the phone can reach the computer.
3. To use it away from home, put HTTPS in front of it (Tailscale, a Cloudflare Tunnel, or hosting)
   and set `GOOP_ACCESS_KEY=<long random string>`. Then open it once with `?key=<that string>`.
   Doing this exposes your health data beyond your LAN, so choose deliberately.

## Scores
* **Recovery (0-100%)** is 40% HRV vs the 7-day baseline (50 at baseline, ±25 per 10%), 25% resting HR vs baseline
  (±8 per bpm), 25% Garmin sleep score, and 10% Body Battery at wake. Missing parts are re-weighted.
  Green is 67 or more, yellow is 34-66, red is under 34.
* **Strain (0-21)** is `21 × (1 − e^(−load/250))`, where load = max(sum of Garmin activity training load,
  1.5 × (moderate + 2 × vigorous intensity min)) + 2 per 1,000 steps.
These are Goop's own heuristics, not Whoop's algorithms.

## Deploying to Render (free web service)

Goop runs on Render's free plan with `render.yaml` (Python 3.13, `uvicorn server:app --port $PORT`, health check `/api/status`).

**Cloud mode (`GOOP_CLOUD=1`)**
- `GOOP_ACCESS_KEY` is required on every `/api` route, via the `x-goop-key` header or the `goop_key` cookie. The app shows a key-entry screen on first launch, and the cookie is HttpOnly + Secure + SameSite=Strict. Without a key set, the API answers 503 (fails closed). The only exception is unauthenticated `GET /api/status`, which returns only `{"ok":true,"locked":true}` for Render's health check.
- The home-network-only check is disabled (the key replaces it). Key attempts are limited to 10 per IP per 10 min, and Garmin sign-in/MFA to 5 per IP per 15 min.
- **Tokens:** `GOOP_GARMIN_TOKENS` is a base64 JSON secret produced by `export_tokens.py` on the Mac. **That secret is the source of truth.** Render free has no persistent disk, so tokens Garmin refreshes are kept in memory only, and they're lost on restart, redeploy or idle spin-down. The original secret keeps working because Garmin tokens last about a year. Re-export from the Mac before then, or if Goop shows demo mode with a sign-in error. `GET /api/garmin/tokens` (with the key) shows whether a refresh happened, and never returns token material.
- The in-app Garmin sign-in still works, but its tokens also live in memory only until the next restart. Exporting from the Mac is the reliable path.
- **Cold starts:** a free instance sleeps after 15 min idle, and the first request takes about 50 s to wake it. The PWA's offline shell opens instantly from cache and shows the last-loaded data while the server wakes.
- Keyring isn't used in the cloud (it's only installed on macOS).
