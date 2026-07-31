"""Authentication: credential resolution, password login, sessions, tenancy."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from conftest import TEST_API_KEY, TEST_JWT_SECRET, TEST_PASSWORD
from src.config import RemoteAuthConfig
from src.remote.auth import AuthManager, LoginThrottle


def _manager(**kwargs) -> AuthManager:
    kwargs.setdefault("auth_config", RemoteAuthConfig())
    return AuthManager(**kwargs)


# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------


class TestCredentialResolution:
    def test_env_beats_the_config_file(self):
        """The committed config used to silently win over the environment."""
        manager = _manager(
            auth_config=RemoteAuthConfig(api_key="a-stale-config-key-thats-long-enough")
        )
        client = asyncio.run(manager.authenticate(authorization=f"Bearer {TEST_API_KEY}"))
        assert client.auth_type == "api_key"

    @pytest.mark.parametrize(
        "value",
        [
            "choose_a_long_random_stringchoose_a_long_random_string",
            "changeme_changeme_changeme_changeme",
            "your_api_key_goes_right_here_ok",
            "placeholder_value_that_is_long_enough",
        ],
    )
    def test_placeholder_credentials_are_rejected(self, value, monkeypatch):
        monkeypatch.delenv("PAI_API_KEY", raising=False)
        monkeypatch.delenv("PAI_JWT_SECRET", raising=False)
        manager = _manager(auth_config=RemoteAuthConfig(api_key=value, jwt_secret=value))

        with pytest.raises(HTTPException) as exc:
            asyncio.run(manager.authenticate(authorization=f"Bearer {value}"))
        assert exc.value.status_code == 401

    def test_short_credentials_are_rejected(self, monkeypatch):
        monkeypatch.delenv("PAI_API_KEY", raising=False)
        monkeypatch.delenv("PAI_JWT_SECRET", raising=False)
        manager = _manager(auth_config=RemoteAuthConfig(api_key="short"))
        with pytest.raises(HTTPException):
            asyncio.run(manager.authenticate(authorization="Bearer short"))

    @pytest.mark.parametrize(
        "header", [None, "", "Basic abc", "Bearer", "bearer", "Token abc"]
    )
    def test_malformed_authorization_headers_are_rejected(self, header):
        manager = _manager()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(manager.authenticate(authorization=header))
        assert exc.value.status_code == 401

    def test_wrong_api_key_is_rejected(self):
        manager = _manager()
        with pytest.raises(HTTPException):
            asyncio.run(manager.authenticate(authorization="Bearer not-the-right-key-at-all-x"))


# ---------------------------------------------------------------------------
# Password login
# ---------------------------------------------------------------------------


class TestPasswordLogin:
    def test_correct_password_issues_a_usable_token(self):
        manager = _manager()
        issued = manager.login(TEST_PASSWORD)

        client = asyncio.run(manager.authenticate(authorization=f"Bearer {issued.access_token}"))
        assert client.auth_type == "jwt"
        assert client.user_id == issued.user_id

    def test_wrong_password_is_rejected(self):
        manager = _manager()
        with pytest.raises(HTTPException) as exc:
            manager.login("not the password")
        assert exc.value.status_code == 401

    @pytest.mark.parametrize("bad", ["set_your_password_here", "your_password", "abc", ""])
    def test_placeholder_and_short_passwords_disable_login(self, bad, monkeypatch):
        monkeypatch.setenv("PAI_PASSWORD", bad)
        manager = _manager()
        assert not manager.password_login_available

        with pytest.raises(HTTPException) as exc:
            manager.login(bad)
        assert exc.value.status_code == 503
        assert "PAI_PASSWORD" in exc.value.detail

    def test_login_needs_a_jwt_secret(self, monkeypatch):
        monkeypatch.delenv("PAI_JWT_SECRET", raising=False)
        manager = _manager()
        assert not manager.password_login_available

    def test_expired_token_is_distinguishable(self):
        manager = _manager()
        expired = manager.issue_token("test_user", days=-1).access_token

        with pytest.raises(HTTPException) as exc:
            asyncio.run(manager.authenticate(authorization=f"Bearer {expired}"))
        assert exc.value.status_code == 401
        assert exc.value.detail == "Session expired"

    def test_tampered_token_is_rejected(self):
        manager = _manager()
        token = manager.issue_token("test_user").access_token
        tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")

        with pytest.raises(HTTPException) as exc:
            asyncio.run(manager.authenticate(authorization=f"Bearer {tampered}"))
        assert exc.value.detail == "Invalid token"

    def test_token_signed_with_another_secret_is_rejected(self):
        other = AuthManager(
            auth_config=RemoteAuthConfig(), jwt_secret="a-completely-different-secret-xx"
        )
        foreign = other.issue_token("attacker").access_token

        manager = _manager(jwt_secret=TEST_JWT_SECRET)
        with pytest.raises(HTTPException):
            asyncio.run(manager.authenticate(authorization=f"Bearer {foreign}"))


# ---------------------------------------------------------------------------
# Brute-force throttle
# ---------------------------------------------------------------------------


class TestLoginThrottle:
    def test_free_attempts_then_backoff(self):
        throttle = LoginThrottle(free_attempts=3, base_delay=10.0)
        for _ in range(3):
            throttle.record_failure()
            assert throttle.retry_after() == 0

        throttle.record_failure()
        first = throttle.retry_after()
        assert first > 0

        throttle.record_failure()
        assert throttle.retry_after() > first, "delay must grow"

    def test_success_clears_the_counter(self):
        throttle = LoginThrottle(free_attempts=1, base_delay=10.0)
        throttle.record_failure()
        throttle.record_failure()
        assert throttle.retry_after() > 0

        throttle.record_success()
        assert throttle.retry_after() == 0

    def test_lockout_blocks_even_the_correct_password(self):
        manager = _manager()
        for _ in range(8):
            with pytest.raises(HTTPException):
                manager.login("wrong")

        with pytest.raises(HTTPException) as exc:
            manager.login(TEST_PASSWORD)
        assert exc.value.status_code == 429
        assert "Retry-After" in (exc.value.headers or {})
