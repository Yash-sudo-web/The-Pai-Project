"""Memory domain, undo, idempotency, food matching, review, nudges, metrics."""

from __future__ import annotations

import datetime
import uuid

import pytest

from src.context import LOCAL_TZ, user_scope
from src.domains.memory.tools import ForgetTool, RecallTool, RememberTool, UndoLastTool
from src.domains.nutrition import food_db
from src.domains.review import nudges
from src.domains.review.aggregate import TodaySnapshot, weekly_stats
from src.memory import idempotency
from src.memory.db import Meal, SessionLocal, Task, Workout

USER = "features_user"


# ---------------------------------------------------------------------------
# Memory domain
# ---------------------------------------------------------------------------


class TestMemoryTools:
    def test_remember_then_recall(self):
        with user_scope(USER):
            RememberTool().execute({"content": "I am allergic to shellfish", "category": "fact"})
            RememberTool().execute({"content": "My gym closes on Sundays", "category": "routine"})

            found = RecallTool().execute({"query": "allergic"})

        assert found["count"] == 1
        assert "shellfish" in found["memories"][0]["content"]
        assert found["memories"][0]["category"] == "fact"

    def test_recall_without_a_query_lists_recent(self):
        with user_scope(USER):
            RememberTool().execute({"content": "fact one"})
            RememberTool().execute({"content": "fact two"})
            found = RecallTool().execute({})
        assert found["count"] == 2

    def test_recall_filters_by_category(self):
        with user_scope(USER):
            RememberTool().execute({"content": "likes oat milk", "category": "preference"})
            RememberTool().execute({"content": "trains at 6am", "category": "routine"})
            found = RecallTool().execute({"category": "preference"})
        assert found["count"] == 1 and "oat milk" in found["memories"][0]["content"]

    def test_restating_a_fact_updates_instead_of_duplicating(self):
        with user_scope(USER):
            first = RememberTool().execute({"content": "I run on Tuesdays"})
            second = RememberTool().execute({"content": "i run on tuesdays"})
            found = RecallTool().execute({"query": "tuesdays"})

        assert second["replaced"] is True
        assert second["id"] == first["id"]
        assert found["count"] == 1

    def test_memories_are_scoped_to_the_user(self):
        with user_scope("user_a"):
            RememberTool().execute({"content": "private to a"})
        with user_scope("user_b"):
            assert RecallTool().execute({"query": "private"})["count"] == 0

    def test_forget_removes_a_memory(self):
        with user_scope(USER):
            RememberTool().execute({"content": "temporary detail"})
            removed = ForgetTool().execute({"query": "temporary"})
            remaining = RecallTool().execute({"query": "temporary"})

        assert removed["count"] == 1
        assert remaining["count"] == 0

    def test_forget_needs_an_id_or_query(self):
        with user_scope(USER), pytest.raises(ValueError):
            ForgetTool().execute({})

    def test_remember_rejects_empty_content(self):
        with user_scope(USER), pytest.raises(ValueError):
            RememberTool().execute({"content": "   "})

    def test_remember_rollback_deletes_the_note(self):
        tool = RememberTool()
        with user_scope(USER):
            created = tool.execute({"content": "undo me"})
            tool.rollback({"output": created})
            assert RecallTool().execute({"query": "undo"})["count"] == 0


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------


class TestUndoLast:
    def test_undoes_the_most_recent_action(self, runtime):
        registry, audit = runtime.tool_registry, runtime.audit_log

        with user_scope(USER):
            log_meal = registry.get("nutrition.log_meal")
            result = log_meal.execute({"food_item": "banana", "quantity": 100, "unit": "g"})

            _audit(audit, "nutrition.log_meal", {"food_item": "banana"}, result, "nutrition")

            undone = UndoLastTool(registry, audit).execute({})

        assert undone["undone"] is True
        assert undone["tool_name"] == "nutrition.log_meal"
        assert "banana" in undone["description"]

        with SessionLocal() as db:
            assert db.query(Meal).filter(Meal.id == result["id"]).first() is None

    def test_reports_when_there_is_nothing_to_undo(self, runtime):
        with user_scope(USER):
            result = UndoLastTool(runtime.tool_registry, runtime.audit_log).execute({})
        assert result["undone"] is False
        assert "Nothing recent" in result["description"]

    def test_refuses_tools_that_cannot_be_undone(self, runtime):
        with user_scope(USER):
            result = UndoLastTool(runtime.tool_registry, runtime.audit_log).execute(
                {"tool_name": "gym.check_pr"}
            )
        assert result["undone"] is False and "cannot be undone" in result["description"]

    def test_failed_actions_are_not_undone(self, runtime):
        """An entry with an error never applied, so it must be skipped."""
        audit = runtime.audit_log
        _audit(audit, "nutrition.log_meal", {"food_item": "x"}, None, "nutrition", error="boom")

        with user_scope(USER):
            result = UndoLastTool(runtime.tool_registry, audit).execute({})
        assert result["undone"] is False


def _audit(audit_log, tool_name, inputs, output, domain, error=None):
    from src.types import AuditEntry, DomainName

    audit_log.record(
        AuditEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.datetime.now(datetime.UTC),
            tool_name=tool_name,
            domain=DomainName(domain),
            inputs=inputs,
            output=output,
            error=error,
            approval_status="not_required",
            session_id="test",
        )
    )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_first_reservation_is_acquired(self):
        assert idempotency.reserve(USER, "key-1").acquired is True

    def test_second_attempt_while_running_is_blocked(self):
        idempotency.reserve(USER, "key-2")
        second = idempotency.reserve(USER, "key-2")
        assert second.acquired is False and second.in_progress is True

    def test_completed_key_replays_the_response(self):
        idempotency.reserve(USER, "key-3")
        idempotency.complete(USER, "key-3", {"status": "completed", "result": {"message": "hi"}})

        replay = idempotency.reserve(USER, "key-3")
        assert replay.acquired is False
        assert replay.replay == {"status": "completed", "result": {"message": "hi"}}

    def test_release_frees_the_key_for_a_retry(self):
        idempotency.reserve(USER, "key-4")
        idempotency.release(USER, "key-4")
        assert idempotency.reserve(USER, "key-4").acquired is True

    def test_keys_are_scoped_per_user(self):
        idempotency.reserve("user_a", "shared-key")
        assert idempotency.reserve("user_b", "shared-key").acquired is True

    def test_endpoint_replays_instead_of_running_twice(self, client, auth_headers, runtime):
        from conftest import FakeLLM

        runtime.orchestrator._llm = FakeLLM([("First reply.", []), ("Second reply.", [])])
        runtime.orchestrator._chat_client._llm = runtime.orchestrator._llm
        headers = {**auth_headers, "Idempotency-Key": "mobile-retry-1"}

        first = client.post("/command", headers=headers, json={"command": "log it"})
        second = client.post("/command", headers=headers, json={"command": "log it"})

        assert first.json() == second.json()
        assert first.json()["result"]["message"] == "First reply."
        assert len(runtime.orchestrator._llm.calls) == 1, "the command must run once"

    def test_requests_without_a_key_are_unaffected(self, client, auth_headers, runtime):
        from conftest import FakeLLM

        runtime.orchestrator._llm = FakeLLM([("A.", []), ("B.", [])])
        runtime.orchestrator._chat_client._llm = runtime.orchestrator._llm

        first = client.post("/command", headers=auth_headers, json={"command": "go"})
        second = client.post("/command", headers=auth_headers, json={"command": "go"})
        assert first.json()["result"]["message"] == "A."
        assert second.json()["result"]["message"] == "B."


# ---------------------------------------------------------------------------
# Food matching
# ---------------------------------------------------------------------------


class TestFoodMatching:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("chicken breast", "chicken breast"),
            ("grilled chicken breast", "chicken breast"),
            ("Grilled Chicken Breast", "chicken breast"),
            ("chiken brest", "chicken breast"),  # typo tolerance
            ("eggs", "egg"),
            ("boiled egg", "egg"),
            ("sweet potatoes", "sweet potato"),
            ("raw spinach", "spinach"),
            ("peanut-butter", "peanut butter"),
            ("whey protein powder", "whey protein"),
        ],
    )
    def test_local_matches_without_an_llm_call(self, query, expected):
        matched = food_db.match_local(query)
        assert matched is not None, f"{query!r} should have matched locally"
        assert matched[0] == expected

    @pytest.mark.parametrize("query", ["unicorn steak", "", "   ", "zzzzzz"])
    def test_genuine_misses_fall_through(self, query):
        assert food_db.match_local(query) is None

    def test_exact_match_is_preferred_over_fuzzy(self):
        assert food_db.match_local("rice")[0] == "rice"
        assert food_db.match_local("brown rice")[0] == "brown rice"


# ---------------------------------------------------------------------------
# Weekly review
# ---------------------------------------------------------------------------


class TestWeeklyReview:
    def test_aggregates_across_domains(self):
        now = datetime.datetime.now(datetime.UTC)
        with SessionLocal() as db:
            db.add(
                Workout(
                    id=str(uuid.uuid4()), user_id=USER, exercise="squat",
                    sets=3, reps=10, weight_kg=100.0,
                    worked_out_at=now - datetime.timedelta(days=1), logged_at=now,
                )
            )
            db.add(
                Meal(
                    id=str(uuid.uuid4()), user_id=USER, food_item="rice", quantity=200,
                    unit="g", calories=260, protein_g=5.4, carbs_g=56, fat_g=0.6,
                    is_custom=False, eaten_at=now - datetime.timedelta(days=1), logged_at=now,
                )
            )
            db.add(
                Task(
                    id=str(uuid.uuid4()), user_id=USER, title="ship it",
                    status="completed", created_at=now, completed_at=now,
                )
            )
            db.commit()

        stats = weekly_stats(USER, days=7).as_dict()

        assert stats["gym"]["days_trained"] == 1
        assert stats["gym"]["exercises"] == ["squat"]
        assert stats["gym"]["total_volume_kg"] == 3000.0
        assert stats["nutrition"]["days_logged"] == 1
        assert stats["nutrition"]["avg_calories"] == 260.0
        assert stats["productivity"]["tasks_completed"] == 1

    def test_empty_week_returns_zeroes_not_errors(self):
        stats = weekly_stats("nobody", days=7).as_dict()
        assert stats["gym"]["days_trained"] == 0
        assert stats["nutrition"]["avg_calories"] == 0.0

    def test_excludes_activity_outside_the_window(self):
        old = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
        with SessionLocal() as db:
            db.add(
                Workout(
                    id=str(uuid.uuid4()), user_id=USER, exercise="ancient",
                    sets=1, reps=1, weight_kg=1.0, worked_out_at=old, logged_at=old,
                )
            )
            db.commit()
        assert weekly_stats(USER, days=7).workout_days == 0


# ---------------------------------------------------------------------------
# Nudges
# ---------------------------------------------------------------------------


def _snapshot(**overrides) -> TodaySnapshot:
    base = dict(
        calories=0.0, protein_g=0.0, water_ml=0.0, goal=None,
        days_since_workout=None, overdue_tasks=[], pending_tasks=0,
    )
    base.update(overrides)
    return TodaySnapshot(**base)


# Wall-clock times in the user's zone. Passing UTC here would put "evening"
# in the middle of their night — the rules localise before comparing.
EVENING = datetime.datetime(2026, 7, 30, 21, tzinfo=LOCAL_TZ)
MORNING = datetime.datetime(2026, 7, 30, 9, tzinfo=LOCAL_TZ)


class TestNudges:
    def test_quiet_when_nothing_is_wrong(self):
        assert nudges.evaluate(_snapshot(), MORNING) == []

    def test_a_blank_day_is_reported_in_the_evening(self):
        """No goal, no tasks, no history — a blank day still deserves a word."""
        found = nudges.evaluate(_snapshot(), EVENING)
        assert [n.kind for n in found] == ["nothing_logged"]

    def test_protein_behind_only_fires_in_the_evening(self):
        snapshot = _snapshot(goal={"protein_g": 150}, protein_g=40)
        assert nudges.evaluate(snapshot, MORNING) == []
        found = nudges.evaluate(snapshot, EVENING)
        assert [n.kind for n in found] == ["protein_behind"]
        assert "110g short" in found[0].message

    def test_protein_close_to_target_is_not_flagged(self):
        snapshot = _snapshot(goal={"protein_g": 150}, protein_g=140)
        assert nudges.evaluate(snapshot, EVENING) == []

    def test_workout_gap(self):
        near = [n.kind for n in nudges.evaluate(_snapshot(days_since_workout=2), EVENING)]
        assert "workout_gap" not in near
        far = [n.kind for n in nudges.evaluate(_snapshot(days_since_workout=5), EVENING)]
        assert "workout_gap" in far

    def test_overdue_tasks_are_highest_priority(self):
        snapshot = _snapshot(
            overdue_tasks=["file taxes", "call dentist"],
            goal={"protein_g": 150},
            protein_g=10,
        )
        found = nudges.evaluate(snapshot, EVENING)
        assert found[0].kind == "overdue_tasks" and found[0].priority == "high"
        assert "+1 more" in found[0].message

    def test_calories_over_target(self):
        found = nudges.evaluate(_snapshot(goal={"calories": 2000}, calories=2500), MORNING)
        assert [n.kind for n in found] == ["calories_over"]

    def test_no_goal_means_no_macro_nudges(self):
        assert nudges.evaluate(_snapshot(calories=9000, protein_g=0), EVENING) == []

    def test_due_for_user_reads_the_database(self):
        # An explicit morning instant: the evening rules fire on an empty
        # database by design, so a wall-clock call would pass or fail
        # depending on when the suite runs.
        assert nudges.due_for_user("nobody", now=MORNING) == []


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    async def test_command_paths_are_recorded(self, orchestrator, session):
        from conftest import FakeLLM
        from src.observability import metrics_registry

        metrics_registry.reset()

        await orchestrator.run("hi", session)  # fast path
        orchestrator._llm = FakeLLM([("A reply.", [])])
        orchestrator._chat_client._llm = orchestrator._llm
        await orchestrator.run("say something", session)  # chat

        snapshot = metrics_registry.snapshot()
        assert snapshot["commands"] == 2
        assert snapshot["by_path"] == {"fast_path": 1, "chat": 1}
        assert snapshot["latency_ms"]["p50"] >= 0
        assert "plan" in snapshot["avg_phase_ms"]

    async def test_tool_usage_is_counted(self, orchestrator, session):
        from conftest import FakeLLM, make_tool, tool_call
        from src.observability import metrics_registry

        metrics_registry.reset()
        orchestrator._tool_registry.register(make_tool("t.metric"))
        orchestrator._llm = FakeLLM([(None, [tool_call("t__metric", {})])])
        orchestrator._chat_client._llm = orchestrator._llm

        await orchestrator.run("do it", session)

        snapshot = metrics_registry.snapshot()
        assert snapshot["tool_calls"] == {"t.metric": 1}
        assert "tools" in snapshot["avg_phase_ms"]
        assert snapshot["by_path"] == {"tools": 1}

    def test_metrics_endpoint_requires_auth(self, client, auth_headers):
        assert client.get("/metrics").status_code == 401
        assert client.get("/metrics", headers=auth_headers).status_code == 200

    def test_nudges_endpoint(self, client, auth_headers):
        assert client.get("/nudges").status_code == 401
        body = client.get("/nudges", headers=auth_headers).json()
        assert "nudges" in body and "count" in body
