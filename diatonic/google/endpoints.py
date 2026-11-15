"""Google API base URLs. DIATONIC_GOOGLE_FAKE points everything at a local stand-in (tests and demos)."""
from __future__ import annotations

import os


def urls() -> dict:
    fake = os.environ.get("DIATONIC_GOOGLE_FAKE", "").rstrip("/")
    if fake:
        return {"auth": f"{fake}/o/oauth2/v2/auth", "token": f"{fake}/token", "userinfo": f"{fake}/oauth2/v3/userinfo",
                "revoke": f"{fake}/revoke", "cal": f"{fake}/calendar/v3", "gmail": f"{fake}/gmail/v1/users/me"}
    return {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
            "userinfo": "https://openidconnect.googleapis.com/v1/userinfo", "revoke": "https://oauth2.googleapis.com/revoke",
            "cal": "https://www.googleapis.com/calendar/v3", "gmail": "https://gmail.googleapis.com/gmail/v1/users/me"}
