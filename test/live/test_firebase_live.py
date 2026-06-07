"""Real-Firebase verification (Lift 1 spec §2): the unit suite fakes _verify_token;
this proves the genuine path — project config, aud claim, ADC, real claim shapes.

Operator-run only:
  TOKEN=$(GOOGLE_CLOUD_PROJECT=... FIREBASE_WEB_API_KEY=... python infra/get_firebase_token.py you@example.com)
  FIREBASE_TEST_ID_TOKEN="$TOKEN" pytest test/live -m live
"""

import os

import pytest

pytestmark = pytest.mark.live


def test_real_firebase_token_verifies():
    token = os.environ.get("FIREBASE_TEST_ID_TOKEN")
    if not token:
        pytest.skip("set FIREBASE_TEST_ID_TOKEN (mint one with infra/get_firebase_token.py)")
    from agentic_librarian.api import auth as auth_module

    decoded = auth_module._verify_token(token)
    assert decoded["uid"]
    assert decoded.get("email"), "token must carry email for claim-by-email"
    assert decoded.get("email_verified") is True, "claim-by-email requires email_verified"
