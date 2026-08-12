# Notifications

Nudges arrive as **on-device scheduled notifications**, not server push.

Remote push needs APNs, which needs a paid Apple Developer membership. The FCM
implementation is built and tested but dormant — see
[push-notifications.md](push-notifications.md) for switching it on later.

## How it works

```
app (running)  ──GET /nudges/schedule──►  server
                                            │ evaluate(snapshot, slot_time)
                                            │ one nudge per slot
      ◄────── slots + refresh_after ────────┘
        │
        └─ zonedSchedule ──► iOS holds it ──► fires at 08:10 / 19:40
                                              (even if the app has died)
```

Two slots a day, at **08:10** and **19:40** local — one notification each,
which is the whole interruption budget. Both sit outside quiet hours by
construction, so no separate quiet-hours check is needed on this path.

## Why it re-asks instead of scheduling once

iOS fires a notification with whatever text it was handed. The app gets no
chance to revise it at fire time — there is no hook, and the extension that
*can* rewrite content only works for remote pushes.

So the plan is refreshed repeatedly. The server chooses the cadence and
returns it as `refresh_after_seconds`: 15 minutes when nothing is imminent,
tightening so a refresh always lands ~2 minutes before a slot. That last
refresh is what puts a current number in the notification.

Freshness is therefore bounded by ~2 minutes **while the app is running**, and
by however long ago it died otherwise.

## What the always-on app buys you

This path leans on the app being alive, which the wake word already requires.
The two features share that: without it, notification text would be hours
stale; with it, `protein_behind` carries tonight's real shortfall and
`nothing_logged` is cancelled the moment you log something.

When the app is dead, the last-scheduled notifications still fire — stale, but
present. That degradation is deliberate: a stale nudge beats silence.

## What gets scheduled

Only `channel: push` nudges. The two in-app ones (`calories_over`,
`water_behind`) are still returned by `GET /nudges` and never scheduled —
both are retrospective, and both need an active nutrition goal to exist at
all, so they are silent until you set one.

Each slot takes **one** nudge:

- **Morning (08:10)** — `morning_brief` wins if due, because it is a digest of
  the individual morning rules and firing it alongside its own parts would say
  the same thing twice. Otherwise the highest-priority nudge.
- **Evening (19:40)** — `nothing_logged` wins if the day was blank, for the
  same digest reason: it absorbs the workout gap into its own message so a
  blank day costs one notification rather than two. Otherwise highest
  priority: `overdue_tasks` (high), then `protein_behind` / `workout_gap`
  (medium), then `weekly_review` (low).

  `nothing_logged` asks for nothing first — no goal, no tasks, no history. A
  blank day is reported on its own merits.

Tunables live at the top of `src/domains/review/nudges.py`; the refresh cadence
is in `src/remote/api.py` (`NUDGE_REFRESH_SECONDS`, `NUDGE_PRE_SLOT_SECONDS`).

## Setup

1. Build to a physical iPhone.
2. **Settings → Scheduled nudges** → on. Accept the permission prompt.
3. **Send a test notification** to confirm the path works immediately.

No Apple account, no Firebase, no server credentials. The migration
(`alembic upgrade head`) is still worth running — it adds the tables the
dormant FCM path needs — but nothing here requires them.

The settings row shows `N scheduled · checked Xm ago`, which is the quickest
way to tell whether refreshing is actually happening.

## Failure behaviour

| Situation | What happens |
| --- | --- |
| Server unreachable | The existing schedule is **kept**, not cancelled. A stale nudge beats a silently cancelled one. |
| Session expired (401) | Scheduling stops until you sign in again. |
| Permission denied | The toggle switches itself back off and says why. |
| Nudge stops being true | Cancelled on the next refresh, so it never fires. |
| App killed / rebooted / expired | Already-scheduled notifications still fire, with whatever text they last had. |
| Notifications turned off | Everything queued is cancelled immediately. |

## Known limits

**Content can be stale.** A slot is evaluated against *today's* snapshot, so a
plan built in the morning for tonight reflects the morning. The refresh loop
closes this to ~2 minutes while the app runs; it does not close it at all while
the app is dead.

**Tomorrow's morning is guessed.** When the evening slot has passed, the next
morning slot is planned from today's data — `days_since_workout` will be a day
behind until the app refreshes after midnight.

**The UTC/IST day boundary still applies.** `aggregate.today_snapshot` bounds
"today" in UTC while the rules judge the hour in IST, so anything logged
between midnight and 05:30 counts toward the previous day. Predates this work;
noted in [push-notifications.md](push-notifications.md).
