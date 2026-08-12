"""Push delivery: the new nudge rules, the interruption budget, and the API.

The rules and the selection policy are pure, so most of this needs no
database and no Firebase. The endpoint tests use a fake sender rather than
touching the network.
"""

from __future__ import annotations

import datetime

import pytest

from src.context import LOCAL_TZ
from src.domains.review import nudges
from src.domains.review.aggregate import TodaySnapshot
from src.memory import devices
from src.remote.push import PushResult, PushSender, _error_status

USER = "default_user"


def _snapshot(**overrides) -> TodaySnapshot:
    base = dict(
        calories=0.0, protein_g=0.0, water_ml=0.0, goal=None,
        days_since_workout=None, overdue_tasks=[], pending_tasks=0,
    )
    base.update(overrides)
    return TodaySnapshot(**base)


def _at(hour: int, day: int = 30, month: int = 7) -> datetime.datetime:
    """A local-time instant. 2026-07-30 is a Thursday; the 2nd of August is a Sunday."""
    return datetime.datetime(2026, month, day, hour, tzinfo=LOCAL_TZ)


EVENING = _at(21)
MORNING = _at(9)
NIGHT = _at(23)
SUNDAY_EVENING = _at(20, day=2, month=8)


# ---------------------------------------------------------------------------
# Channel split — what is allowed to interrupt
# ---------------------------------------------------------------------------


class TestChannels:
    def test_retrospective_nudges_never_push(self):
        """Calories already eaten and water already missed offer nothing to act on."""
        found = nudges.evaluate(
            _snapshot(goal={"calories": 2000, "water_ml": 3000}, calories=2500, protein_g=1),
            EVENING,
        )
        by_kind = {n.kind: n for n in found}
        assert by_kind["calories_over"].channel == "in_app"
        assert by_kind["water_behind"].channel == "in_app"

    def test_overdue_tasks_push(self):
        found = nudges.evaluate(_snapshot(overdue_tasks=["file taxes"]), EVENING)
        assert found[0].kind == "overdue_tasks"
        assert found[0].channel == "push"

    def test_workout_gap_pushes(self):
        evening = nudges.evaluate(_snapshot(days_since_workout=5), EVENING)
        gap = next(n for n in evening if n.kind == "workout_gap")
        assert gap.channel == "push"

    def test_morning_brief_still_carries_the_gap(self):
        morning = nudges.evaluate(_snapshot(days_since_workout=5), MORNING)
        brief = next(n for n in morning if n.kind == "morning_brief")
        assert brief.channel == "push"
        assert "no workout in 5 days" in brief.message.lower()


# ---------------------------------------------------------------------------
# New rules
# ---------------------------------------------------------------------------


class TestMorningBrief:
    def test_only_fires_in_the_morning_window(self):
        snapshot = _snapshot(pending_tasks=3)
        for hour in (6, 11, 15, 21):
            kinds = [n.kind for n in nudges.evaluate(snapshot, _at(hour))]
            assert "morning_brief" not in kinds, f"fired at {hour}:00"
        assert "morning_brief" in [n.kind for n in nudges.evaluate(snapshot, MORNING)]

    def test_stays_silent_when_there_is_nothing_to_report(self):
        assert nudges.evaluate(_snapshot(), MORNING) == []

    def test_summarises_several_things_in_one_line(self):
        found = nudges.evaluate(
            _snapshot(overdue_tasks=["taxes"], pending_tasks=4, days_since_workout=6),
            MORNING,
        )
        brief = next(n for n in found if n.kind == "morning_brief")
        assert "1 overdue" in brief.message
        assert "4 tasks pending" in brief.message
        assert "no workout in 6 days" in brief.message


class TestNothingLogged:
    def test_fires_when_a_tracking_user_logs_nothing(self):
        found = nudges.evaluate(_snapshot(goal={"calories": 2000}), EVENING)
        assert "nothing_logged" in [n.kind for n in found]

    def test_any_logged_activity_silences_it(self):
        for logged in ({"calories": 400.0}, {"water_ml": 500.0}, {"days_since_workout": 0}):
            snapshot = _snapshot(goal={"calories": 2000}, **logged)
            kinds = [n.kind for n in nudges.evaluate(snapshot, EVENING)]
            assert "nothing_logged" not in kinds, logged

    def test_fires_without_a_goal_or_any_tasks(self):
        """A blank day is worth mentioning even with nothing else configured."""
        assert [n.kind for n in nudges.evaluate(_snapshot(), EVENING)] == ["nothing_logged"]

    def test_absorbs_the_workout_gap_so_it_costs_one_notification(self):
        found = nudges.evaluate(_snapshot(days_since_workout=6), EVENING)
        blank = next(n for n in found if n.kind == "nothing_logged")
        assert "No workout in 6 days" in blank.message

    def test_does_not_fire_before_evening(self):
        kinds = [n.kind for n in nudges.evaluate(_snapshot(goal={"calories": 2000}), MORNING)]
        assert "nothing_logged" not in kinds


class TestWeeklyReview:
    def test_fires_sunday_evening_only(self):
        snapshot = _snapshot(pending_tasks=1)
        assert "weekly_review" in [n.kind for n in nudges.evaluate(snapshot, SUNDAY_EVENING)]
        assert "weekly_review" not in [n.kind for n in nudges.evaluate(snapshot, EVENING)]

    def test_waits_a_week_before_repeating(self):
        found = nudges.evaluate(_snapshot(pending_tasks=1), SUNDAY_EVENING)
        review = next(n for n in found if n.kind == "weekly_review")
        assert review.dedup_hours == 6 * 24

    def test_empty_account_gets_no_review(self):
        kinds = [n.kind for n in nudges.evaluate(_snapshot(), SUNDAY_EVENING)]
        assert "weekly_review" not in kinds


# ---------------------------------------------------------------------------
# The interruption budget
# ---------------------------------------------------------------------------


class TestSelectForPush:
    def test_quiet_hours_drop_everything(self):
        due = nudges.evaluate(_snapshot(overdue_tasks=["taxes"]), NIGHT)
        selection = nudges.select_for_push(due, NIGHT)
        assert selection.to_send == []
        assert selection.suppressed["overdue_tasks"] == "quiet_hours"

    def test_quiet_hours_wrap_across_midnight(self):
        for hour in (22, 23, 0, 5, 6):
            due = nudges.evaluate(_snapshot(overdue_tasks=["taxes"]), _at(hour))
            assert nudges.select_for_push(due, _at(hour)).to_send == [], hour
        assert nudges.select_for_push(
            nudges.evaluate(_snapshot(overdue_tasks=["taxes"]), _at(7)), _at(7)
        ).to_send

    def test_in_app_nudges_are_never_selected(self):
        due = nudges.evaluate(_snapshot(goal={"calories": 2000}, calories=9000), EVENING)
        selection = nudges.select_for_push(due, EVENING)
        assert selection.to_send == []
        assert selection.suppressed["calories_over"] == "in_app_only"

    def test_daily_cap_limits_interruptions(self):
        due = nudges.evaluate(
            _snapshot(overdue_tasks=["taxes"], goal={"protein_g": 150}, protein_g=10),
            EVENING,
        )
        assert len([n for n in due if n.channel == "push"]) >= 2

        selection = nudges.select_for_push(due, EVENING, sent_in_last_day=1)
        assert len(selection.to_send) == 1
        # The highest-priority one survives; evaluate() sorts before we filter.
        assert selection.to_send[0].kind == "overdue_tasks"
        assert "daily_cap" in selection.suppressed.values()

    def test_cap_already_spent_sends_nothing(self):
        due = nudges.evaluate(_snapshot(overdue_tasks=["taxes"]), EVENING)
        selection = nudges.select_for_push(
            due, EVENING, sent_in_last_day=nudges.MAX_PUSHES_PER_DAY
        )
        assert selection.to_send == []

    def test_recently_sent_kinds_are_skipped(self):
        due = nudges.evaluate(_snapshot(overdue_tasks=["taxes"], calories=500), EVENING)
        selection = nudges.select_for_push(due, EVENING, already_sent={"overdue_tasks"})
        assert selection.to_send == []
        assert selection.suppressed["overdue_tasks"] == "recently_sent"

    def test_dedup_cutoffs_use_each_kinds_own_window(self):
        due = nudges.evaluate(_snapshot(pending_tasks=1), SUNDAY_EVENING)
        cutoffs = nudges.dedup_cutoffs(due, SUNDAY_EVENING)
        assert cutoffs["weekly_review"] == SUNDAY_EVENING - datetime.timedelta(days=6)


# ---------------------------------------------------------------------------
# Delivery ledger and token registry
# ---------------------------------------------------------------------------


class TestDeviceStore:
    def test_register_is_idempotent(self):
        assert devices.register(USER, "tok-1", "ios") is True
        assert devices.register(USER, "tok-1", "ios") is False
        assert devices.tokens_for(USER) == ["tok-1"]

    def test_rejects_unknown_platforms(self):
        with pytest.raises(ValueError):
            devices.register(USER, "tok-x", "windows")

    def test_a_token_moves_with_its_latest_owner(self):
        devices.register("alice", "shared", "ios")
        devices.register("bob", "shared", "ios")
        assert devices.tokens_for("alice") == []
        assert devices.tokens_for("bob") == ["shared"]

    def test_unregister_many_prunes_dead_tokens(self):
        devices.register(USER, "a", "ios")
        devices.register(USER, "b", "android")
        assert devices.unregister_many(["a", "b", "never-existed"]) == 2
        assert devices.tokens_for(USER) == []

    def test_ledger_reports_the_most_recent_send(self):
        now = datetime.datetime.now(datetime.UTC)
        old = now - datetime.timedelta(hours=30)
        devices.record_sent(USER, "overdue_tasks", old)
        devices.record_sent(USER, "overdue_tasks", now)

        seen = devices.last_sent_map(USER, ["overdue_tasks"], now - datetime.timedelta(days=2))
        assert seen["overdue_tasks"] >= now - datetime.timedelta(seconds=5)

    def test_ledger_ignores_sends_outside_the_window(self):
        now = datetime.datetime.now(datetime.UTC)
        devices.record_sent(USER, "overdue_tasks", now - datetime.timedelta(days=3))
        assert devices.last_sent_map(USER, ["overdue_tasks"], now - datetime.timedelta(hours=18)) == {}

    def test_daily_count_only_sees_the_window(self):
        now = datetime.datetime.now(datetime.UTC)
        devices.record_sent(USER, "a", now)
        devices.record_sent(USER, "b", now - datetime.timedelta(hours=30))
        assert devices.pushes_since(USER, now - datetime.timedelta(hours=24)) == 1


# ---------------------------------------------------------------------------
# The sender
# ---------------------------------------------------------------------------


class TestPushSender:
    def test_disabled_without_configuration(self):
        sender = PushSender(project_id=None, credentials_blob=None)
        assert not sender.enabled
        assert "PAI_FCM_PROJECT_ID" in (sender.disabled_reason or "")

    async def test_disabled_sender_is_a_no_op(self):
        sender = PushSender(project_id=None, credentials_blob=None)
        result = await sender.send_to_tokens(["tok"], "t", "b")
        assert result.sent == 0 and result.skipped_reason

    async def test_no_devices_is_not_an_error(self):
        sender = PushSender(project_id="p", credentials_blob={"type": "service_account"})
        result = await sender.send_to_tokens([], "t", "b")
        assert result.skipped_reason == "no registered devices"

    def test_message_envelope_stringifies_data_and_sets_sound(self):
        message = PushSender._build_message("tok", "Title", "Body", {"kind": "x", "n": 3})
        assert message["message"]["token"] == "tok"
        assert message["message"]["notification"] == {"title": "Title", "body": "Body"}
        # FCM rejects the whole request if any data value is not a string.
        assert message["message"]["data"] == {"kind": "x", "n": "3"}
        assert message["message"]["apns"]["payload"]["aps"]["sound"] == "default"

    def test_error_status_is_read_from_the_details_array(self):
        class _Response:
            @staticmethod
            def json():
                return {
                    "error": {
                        "status": "NOT_FOUND",
                        "details": [{"errorCode": "UNREGISTERED"}],
                    }
                }

        assert _error_status(_Response()) == "UNREGISTERED"

    def test_unparseable_error_body_does_not_raise(self):
        class _Response:
            @staticmethod
            def json():
                raise ValueError("not json")

        assert _error_status(_Response()) == "UNPARSEABLE"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


class FakeSender:
    """Records sends instead of calling Firebase."""

    def __init__(self, result: PushResult | None = None, enabled: bool = True):
        self.enabled = enabled
        self.disabled_reason = None if enabled else "test"
        self.sends: list[tuple[list[str], str, str]] = []
        self._result = result

    async def send_to_tokens(self, tokens, title, body, data=None):
        self.sends.append((list(tokens), title, body))
        if self._result is not None:
            return self._result
        return PushResult(sent=len(tokens))


@pytest.fixture
def app_with_sender(runtime):
    """Builds a TestClient wired to a given FakeSender."""
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    from src.remote.api import create_app

    @contextmanager
    def _build(sender: FakeSender):
        app = create_app(
            runtime.orchestrator,
            runtime.confirmation_layer,
            runtime.auth_manager,
            runtime.config.permissions,
            session_manager=runtime.chat_session_manager,
            push_sender=sender,
        )
        with TestClient(app) as test_client:
            yield test_client

    return _build


@pytest.fixture
def push_client(app_with_sender):
    """A TestClient whose app pushes through a FakeSender that always succeeds."""
    sender = FakeSender()
    with app_with_sender(sender) as client:
        yield client, sender


@pytest.fixture
def no_quiet_hours(monkeypatch):
    """Neutralise the do-not-disturb window.

    The cron tests run at whatever time the suite happens to be run, and a
    22:00–07:00 IST run would otherwise suppress every push and fail.
    """
    monkeypatch.setattr(nudges, "QUIET_START_HOUR", 24)
    monkeypatch.setattr(nudges, "QUIET_END_HOUR", 0)


class TestDeviceEndpoints:
    def test_register_and_unregister(self, client, auth_headers):
        r = client.post(
            "/devices/register",
            headers=auth_headers,
            json={"token": "abc123", "platform": "ios"},
        )
        assert r.status_code == 200
        assert r.json()["created"] is True

        # Re-registering is the normal case, not an error.
        again = client.post(
            "/devices/register",
            headers=auth_headers,
            json={"token": "abc123", "platform": "ios"},
        )
        assert again.json()["created"] is False

        removed = client.delete("/devices/abc123", headers=auth_headers)
        assert removed.json()["removed"] is True

    def test_register_rejects_a_bad_platform(self, client, auth_headers):
        r = client.post(
            "/devices/register",
            headers=auth_headers,
            json={"token": "abc", "platform": "toaster"},
        )
        assert r.status_code == 400

    def test_register_rejects_an_empty_token(self, client, auth_headers):
        r = client.post(
            "/devices/register",
            headers=auth_headers,
            json={"token": "   ", "platform": "ios"},
        )
        assert r.status_code == 400

    def test_registration_requires_auth(self, client):
        r = client.post("/devices/register", json={"token": "a", "platform": "ios"})
        assert r.status_code == 401


class TestCronEndpoint:
    def test_reachable_by_get_which_is_all_vercel_cron_sends(self, client, auth_headers):
        assert client.get("/cron/nudges", headers=auth_headers).status_code == 200

    def test_also_accepts_post(self, client, auth_headers):
        assert client.post("/cron/nudges", headers=auth_headers).status_code == 200

    def test_requires_a_credential(self, client):
        assert client.get("/cron/nudges").status_code == 401

    def test_accepts_the_vercel_cron_secret(self, client, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", "a-scheduler-secret-value")
        r = client.get(
            "/cron/nudges",
            headers={"Authorization": "Bearer a-scheduler-secret-value"},
        )
        assert r.status_code == 200

    def test_rejects_a_wrong_cron_secret(self, client, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", "a-scheduler-secret-value")
        r = client.get("/cron/nudges", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_reports_that_push_is_unconfigured(self, client, auth_headers):
        """The default sender has no Firebase credentials in the test env."""
        body = client.get("/cron/nudges", headers=auth_headers).json()
        assert body["push_enabled"] is False
        assert body["push_disabled_reason"]

    def test_delivers_and_records_a_due_nudge(
        self, push_client, auth_headers, no_quiet_hours
    ):
        client, sender = push_client
        client.post(
            "/devices/register",
            headers=auth_headers,
            json={"token": "device-1", "platform": "ios"},
        )

        _seed_overdue_task(USER)
        body = client.post("/cron/nudges", headers=auth_headers).json()

        assert "overdue_tasks" in body["delivered"]
        assert sender.sends and sender.sends[0][0] == ["device-1"]

        # The ledger now suppresses a second run inside the dedup window.
        repeat = client.post("/cron/nudges", headers=auth_headers).json()
        assert repeat["delivered"] == []
        assert repeat["suppressed"]["overdue_tasks"] == "recently_sent"

    def test_a_failed_send_does_not_open_the_dedup_window(
        self, app_with_sender, auth_headers, no_quiet_hours
    ):
        """A Firebase outage must not silence tomorrow's attempt."""
        sender = FakeSender(result=PushResult(failed=1, skipped_reason="boom"))
        with app_with_sender(sender) as client:
            client.post(
                "/devices/register",
                headers=auth_headers,
                json={"token": "device-1", "platform": "ios"},
            )
            _seed_overdue_task(USER)

            first = client.post("/cron/nudges", headers=auth_headers).json()
            assert first["delivered"] == []

            second = client.post("/cron/nudges", headers=auth_headers).json()
            assert second["suppressed"].get("overdue_tasks") != "recently_sent"

    def test_dead_tokens_are_pruned(
        self, app_with_sender, auth_headers, no_quiet_hours
    ):
        sender = FakeSender(result=PushResult(failed=1, dead_tokens=["device-1"]))
        with app_with_sender(sender) as client:
            client.post(
                "/devices/register",
                headers=auth_headers,
                json={"token": "device-1", "platform": "ios"},
            )
            _seed_overdue_task(USER)
            body = client.post("/cron/nudges", headers=auth_headers).json()

        assert body["tokens_pruned"] == 1
        assert devices.tokens_for(USER) == []


def _seed_overdue_task(user_id: str) -> None:
    """One task that was due yesterday, so overdue_tasks is guaranteed due."""
    import uuid

    from src.memory.db import SessionLocal, Task

    with SessionLocal() as db:
        db.add(
            Task(
                id=uuid.uuid4().hex,
                user_id=user_id,
                title="file taxes",
                status="pending",
                due_date=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1),
            )
        )
        db.commit()


# ---------------------------------------------------------------------------
# On-device scheduling plan
# ---------------------------------------------------------------------------


class TestSlotTimes:
    def test_returns_the_next_of_each_slot_soonest_first(self):
        # 06:00 local: both of today's slots are still ahead.
        slots = nudges.next_slot_times(_at(6))
        assert [(s.hour, s.minute) for s in slots] == [(8, 10), (19, 40)]
        assert all(s.day == 30 for s in slots)

    def test_a_passed_slot_rolls_to_tomorrow(self):
        # 12:00 local: this morning is gone, tonight is not.
        slots = nudges.next_slot_times(_at(12))
        assert [(s.hour, s.minute) for s in slots] == [(19, 40), (8, 10)]
        assert slots[0].day == 30 and slots[1].day == 31

    def test_slots_sit_outside_quiet_hours(self):
        """One notification per slot is the whole interruption budget."""
        for hour, _ in (nudges.MORNING_SLOT, nudges.EVENING_SLOT):
            assert nudges.QUIET_END_HOUR <= hour < nudges.QUIET_START_HOUR


class TestPlan:
    def test_nothing_due_means_nothing_scheduled(self):
        assert nudges.plan(_snapshot(calories=500, days_since_workout=0), _at(6)) == []

    def test_a_blank_day_schedules_the_evening_slot(self):
        planned = nudges.plan(_snapshot(), _at(6))
        assert [(p.at.hour, p.nudge.kind) for p in planned] == [(19, "nothing_logged")]

    def test_each_slot_carries_at_most_one_notification(self):
        planned = nudges.plan(
            _snapshot(overdue_tasks=["taxes"], goal={"protein_g": 150}, protein_g=10),
            _at(6),
        )
        assert len(planned) == len({p.at for p in planned})
        assert len(planned) <= 2

    def test_morning_slot_prefers_the_digest_over_its_own_parts(self):
        """morning_brief already contains the overdue count, so it wins."""
        planned = nudges.plan(_snapshot(overdue_tasks=["taxes"], pending_tasks=3), _at(6))
        morning = next(p for p in planned if p.at.hour == 8)
        assert morning.nudge.kind == "morning_brief"
        assert "1 overdue" in morning.nudge.message

    def test_evening_slot_takes_the_most_urgent(self):
        planned = nudges.plan(
            _snapshot(overdue_tasks=["taxes"], goal={"protein_g": 150}, protein_g=10),
            _at(6),
        )
        evening = next(p for p in planned if p.at.hour == 19)
        assert evening.nudge.kind == "overdue_tasks"
        assert evening.nudge.priority == "high"

    def test_in_app_nudges_are_never_scheduled(self):
        planned = nudges.plan(
            _snapshot(goal={"calories": 2000, "water_ml": 3000}, calories=9000, protein_g=1),
            _at(6),
        )
        assert all(p.nudge.channel == "push" for p in planned)
        assert "calories_over" not in [p.nudge.kind for p in planned]

    def test_serialises_with_the_fire_time(self):
        planned = nudges.plan(_snapshot(overdue_tasks=["taxes"]), _at(6))
        payload = planned[0].as_dict()
        assert payload["kind"] and payload["title"] and payload["message"]
        assert payload["at"].startswith("2026-07-30T")

    def test_plan_for_user_reads_the_database(self):
        # An empty database is a blank day, so the evening slot is filled.
        planned = nudges.plan_for_user("nobody")
        assert [p.nudge.kind for p in planned] == ["nothing_logged"]


class TestRefreshDelay:
    def test_flat_interval_when_no_slot_is_near(self):
        from src.remote.api import NUDGE_REFRESH_SECONDS, _refresh_delay_seconds

        now = datetime.datetime.now(datetime.UTC)
        far = nudges.PlannedNudge(
            at=now + datetime.timedelta(hours=6),
            nudge=nudges.Nudge(kind="k", message="m", priority="low"),
        )
        assert _refresh_delay_seconds([far], now) == NUDGE_REFRESH_SECONDS

    def test_shortens_so_a_refresh_lands_just_before_the_slot(self):
        from src.remote.api import NUDGE_PRE_SLOT_SECONDS, _refresh_delay_seconds

        now = datetime.datetime.now(datetime.UTC)
        soon = nudges.PlannedNudge(
            at=now + datetime.timedelta(minutes=5),
            nudge=nudges.Nudge(kind="k", message="m", priority="low"),
        )
        delay = _refresh_delay_seconds([soon], now)
        assert delay == 300 - NUDGE_PRE_SLOT_SECONDS

    def test_a_slot_about_to_fire_gets_no_extra_refresh(self):
        """It fires before any refresh could reach it, so wait for the next one."""
        from src.remote.api import NUDGE_REFRESH_SECONDS, _refresh_delay_seconds

        now = datetime.datetime.now(datetime.UTC)
        imminent = nudges.PlannedNudge(
            at=now + datetime.timedelta(seconds=5),
            nudge=nudges.Nudge(kind="k", message="m", priority="low"),
        )
        assert _refresh_delay_seconds([imminent], now) == NUDGE_REFRESH_SECONDS

    def test_never_busy_loops(self):
        from src.remote.api import _refresh_delay_seconds

        now = datetime.datetime.now(datetime.UTC)
        # Ten seconds past the pre-slot lead: without a floor this would ask
        # the client back almost immediately.
        near = nudges.PlannedNudge(
            at=now + datetime.timedelta(seconds=130),
            nudge=nudges.Nudge(kind="k", message="m", priority="low"),
        )
        assert _refresh_delay_seconds([near], now) == 60

    def test_empty_plan_uses_the_flat_interval(self):
        from src.remote.api import NUDGE_REFRESH_SECONDS, _refresh_delay_seconds

        assert _refresh_delay_seconds([]) == NUDGE_REFRESH_SECONDS


class TestScheduleEndpoint:
    def test_returns_a_plan_and_a_refresh_cadence(self, client, auth_headers):
        _seed_overdue_task(USER)
        body = client.get("/nudges/schedule", headers=auth_headers).json()

        assert body["count"] >= 1
        assert body["slots"][0]["at"]
        assert body["slots"][0]["message"]
        assert 60 <= body["refresh_after_seconds"] <= 15 * 60

    def test_a_blank_day_fills_only_the_evening_slot(self, client, auth_headers):
        body = client.get("/nudges/schedule", headers=auth_headers).json()
        assert [s["kind"] for s in body["slots"]] == ["nothing_logged"]

    def test_requires_auth(self, client):
        assert client.get("/nudges/schedule").status_code == 401
