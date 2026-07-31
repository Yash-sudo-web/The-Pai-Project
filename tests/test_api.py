"""HTTP surface: endpoint auth, tenancy, streaming, login endpoints."""

from __future__ import annotations

import datetime
import uuid

import pytest

from conftest import FakeLLM, TEST_PASSWORD

PROTECTED_GETS = [
    "/chat/history",
    "/chat/sessions",
    "/chat/session/whatever/messages",
    "/auth/me",
]


class TestEndpointAuth:
    @pytest.mark.parametrize("path", PROTECTED_GETS)
    def test_protected_gets_reject_anonymous_callers(self, client, path):
        assert client.get(path).status_code == 401

    def test_command_endpoints_reject_anonymous_callers(self, client):
        assert client.post("/command", json={"command": "hi"}).status_code == 401
        assert client.post("/command/stream", json={"command": "hi"}).status_code == 401

    def test_public_endpoints_stay_public(self, client):
        assert client.get("/health").status_code == 200
        assert client.get("/auth/status").status_code == 200
        assert client.get("/").status_code == 200

    def test_bad_key_is_rejected(self, client):
        headers = {"Authorization": "Bearer definitely-not-the-key-xxxxxx"}
        assert client.get("/chat/history", headers=headers).status_code == 401


class TestTenancy:
    def test_cannot_read_another_users_session(self, client, auth_headers):
        from src.memory.db import ChatSession, SessionLocal

        with SessionLocal() as db:
            other = ChatSession(
                id=str(uuid.uuid4()),
                user_id="somebody_else",
                session_date=datetime.date.today(),
                status="active",
                message_count=0,
                created_at=datetime.datetime.now(datetime.UTC),
            )
            db.add(other)
            db.commit()
            other_id = other.id

        assert client.get(f"/chat/session/{other_id}/messages", headers=auth_headers).status_code == 404

    def test_can_read_own_session(self, client, auth_headers):
        own = client.get("/chat/history", headers=auth_headers).json()["session_id"]
        response = client.get(f"/chat/session/{own}/messages", headers=auth_headers)
        assert response.status_code == 200


class TestCommandEndpoints:
    def test_command_returns_the_final_message(self, client, auth_headers, runtime):
        runtime.orchestrator._llm = FakeLLM([("Hello there.", [])])
        runtime.orchestrator._chat_client._llm = runtime.orchestrator._llm

        response = client.post("/command", headers=auth_headers, json={"command": "say hi"})
        assert response.status_code == 200
        assert response.json()["result"]["message"] == "Hello there."

    def test_stream_emits_sse_frames_ending_in_done(self, client, auth_headers, runtime):
        runtime.orchestrator._llm = FakeLLM([("Streamed reply.", [])])
        runtime.orchestrator._chat_client._llm = runtime.orchestrator._llm

        frames = []
        with client.stream(
            "POST", "/command/stream", headers=auth_headers, json={"command": "say hi"}
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            for line in response.iter_lines():
                if line.startswith("event:"):
                    frames.append(line.split(":", 1)[1].strip())

        assert frames[0] == "token"
        assert frames[-1] == "done"
        assert "result" in frames

    @pytest.mark.parametrize("command", ["", "   "])
    def test_empty_command_is_rejected(self, client, auth_headers, command):
        response = client.post("/command", headers=auth_headers, json={"command": command})
        assert response.status_code == 400

    def test_overlong_command_is_rejected(self, client, auth_headers):
        response = client.post(
            "/command", headers=auth_headers, json={"command": "x" * 5000}
        )
        assert response.status_code == 400
        assert "too long" in response.json()["detail"].lower()


class TestAuthEndpoints:
    def test_status_reports_password_login(self, client):
        body = client.get("/auth/status").json()
        assert body["password_login"] is True
        assert body["session_days"] == 30

    def test_login_then_use_the_token(self, client):
        token = client.post("/auth/login", json={"password": TEST_PASSWORD}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        assert client.get("/auth/me", headers=headers).json()["auth_type"] == "jwt"
        assert client.get("/chat/history", headers=headers).status_code == 200

    def test_wrong_password_returns_401(self, client):
        assert client.post("/auth/login", json={"password": "nope"}).status_code == 401

    def test_refresh_issues_a_new_token(self, client):
        token = client.post("/auth/login", json={"password": TEST_PASSWORD}).json()["access_token"]
        response = client.post("/auth/refresh", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json()["access_token"]

    def test_api_keys_cannot_be_refreshed(self, client, auth_headers):
        assert client.post("/auth/refresh", headers=auth_headers).status_code == 400


class TestHealth:
    def test_health_reports_database_and_profile(self, client):
        body = client.get("/health").json()
        assert body["checks"]["database"] == "connected"
        assert body["profile"] == "local"
        assert body["checks"]["streaming"] == "available"
