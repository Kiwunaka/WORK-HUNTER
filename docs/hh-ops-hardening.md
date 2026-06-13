# HH Ops Hardening Notes

Date: 2026-06-09

## Docker Runner

The repository includes a small Dockerfile for local scheduled use:

```bash
docker build -t work-hunter .
docker run --rm -v "%cd%/.work-hunter:/data" work-hunter --root /data hh-auth-status
```

For browser-backed experiments that need Playwright Chromium cached in the image:

```bash
docker build --build-arg INSTALL_PLAYWRIGHT=true -t work-hunter:browser .
```

The image sets `QT_QUICK_BACKEND=software` by default. Keep it for HH auth/browser tooling on headless Linux or GPU-hostile remote desktops.

Line endings are hardened during build with `dos2unix` for shell/docker files. Runtime state should live in the mounted `/data` volume, not inside the image.

## Safe Runner Plan Example

Scheduled runner plans should stay plan-first:

```json
{
  "tasks": [
    {"task": "sync", "sources": ["hh"], "limit": 50},
    {"task": "score", "limit": 1000},
    {"task": "hh-campaign-plan", "limit": 25, "min_score": 80, "ai_filter_mode": "light"},
    {"task": "hh-update-resumes"}
  ]
}
```

`hh-update-resumes` is recorded as `planned` unless the task explicitly passes `confirm: true` or `real: true`.

## Config Example Without Secrets

```json
{
  "hh_account_profile": "default",
  "hh_account_profiles": {
    "default": {
      "access_token": "",
      "refresh_token": "",
      "client_id": "",
      "client_secret": ""
    }
  },
  "sources": {
    "hh": {
      "enabled": true,
      "area": 113,
      "access_token": "",
      "refresh_token": "",
      "client_id": "",
      "client_secret": "",
      "hh_cookie_file": ""
    }
  },
  "hh_agent": {
    "paused": false,
    "telegram": {
      "enabled": false,
      "bot_token": "",
      "allowed_user_ids": [],
      "cockpit_base_url": "http://127.0.0.1:8787"
    }
  }
}
```

Do not bake real tokens, cookies, SMTP passwords, or Telegram bot tokens into Docker images or docs. Put them in the mounted local config only.
