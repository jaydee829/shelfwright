#!/usr/bin/env python3
"""Mint a REAL Firebase ID token for live verification (Lift 1 rollout runbook + the
live-marked test). No browser needed.

Flow: ensure a Firebase user record exists (admin SDK over ADC) → mint a custom token
→ exchange it for an ID token via the Identity Toolkit REST API. The exchanged token
is a genuine Firebase ID token, so verifying it exercises the exact prod code path.

Usage (from the WSL clone, routed through the app container like verify_restore.py):
  GOOGLE_CLOUD_PROJECT=agentic-librarian-prod FIREBASE_WEB_API_KEY=<key> \\
    python infra/get_firebase_token.py jaydee829@gmail.com

Requires: gcloud ADC (gcloud auth application-default login). The Web API key is in
Firebase console → Project settings → General → Web API Key (an identifier, not a
secret — but keep it out of the repo anyway).

VERIFY on the first live run (REC-019 pattern): custom-token-minted ID tokens are
expected to carry email/email_verified from the user record; if claim-by-email 403s,
inspect the decoded token claims first."""

import os
import sys

import firebase_admin
import requests
from firebase_admin import auth


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    email = sys.argv[1].strip().lower()
    api_key = os.environ.get("FIREBASE_WEB_API_KEY")
    if not api_key:
        print("error: set FIREBASE_WEB_API_KEY (Firebase console → Project settings → General)", file=sys.stderr)
        return 2
    firebase_admin.initialize_app()
    try:
        user = auth.get_user_by_email(email)
    except auth.UserNotFoundError:
        user = auth.create_user(email=email, email_verified=True)
    if not user.email_verified:
        auth.update_user(user.uid, email_verified=True)
    custom_token = auth.create_custom_token(user.uid).decode()
    resp = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={api_key}",
        json={"token": custom_token, "returnSecureToken": True},
        timeout=30,
    )
    resp.raise_for_status()
    print(resp.json()["idToken"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
