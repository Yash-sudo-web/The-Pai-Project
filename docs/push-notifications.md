# Push notifications

> **Not the active path.** Nudges are currently delivered as on-device
> scheduled notifications — see [notifications.md](notifications.md). Everything
> below is built and tested but dormant, waiting on a paid Apple Developer
> membership. Follow it when you want to switch remote push on.

Proactive nudges delivered to the phone, rather than sitting in `GET /nudges`
waiting for the app to be opened.

## How it flows

```
scheduler  ──►  GET /cron/nudges
                     │
                     ├─ nudges.due_for_user()      which rules are true now
                     ├─ nudges.select_for_push()   which of those may interrupt
                     ├─ devices.tokens_for()       where to send
                     └─ PushSender.send_to_tokens() ──► FCM ──► APNs ──► iPhone
                              │
                              └─ record_sent() opens the dedup window
```

The rules are pure functions and the delivery policy is a separate pure
function, so both are unit-testable without a database, a clock, or Firebase
credentials — see `tests/test_push.py`.

## What you have to set up

Nothing in the repo can do these for you; they need your Apple and Firebase
accounts.

### 1. Apple — an APNs key

Requires a paid Apple Developer Program membership ($99/yr). Without it, iOS
push cannot work at all.

1. <https://developer.apple.com/account> → **Certificates, Identifiers &
   Profiles** → **Keys** → **+**
2. Name it anything, tick **Apple Push Notifications service (APNs)**, continue,
   register.
3. Download the `.p8` file. **You get exactly one download.** Note the **Key ID**
   next to it and your **Team ID** (top right of the developer portal).
4. Under **Identifiers**, find `com.yashmathur.paiClient` and make sure
   **Push Notifications** is ticked.

### 2. Firebase — a project and an iOS app

1. <https://console.firebase.google.com> → **Add project**.
2. Add an **iOS** app with bundle ID `com.yashmathur.paiClient`.
3. Download **`GoogleService-Info.plist`** and place it at
   `client/ios/Runner/GoogleService-Info.plist`. It must be added to the Runner
   target in Xcode (drag it into the Runner group, tick "Copy items if needed"
   and the Runner target) — dropping it in the folder alone is not enough, the
   iOS SDK reads it from the app bundle.
4. **Project settings → Cloud Messaging → APNs Authentication Key → Upload**.
   Upload the `.p8` with the Key ID and Team ID from step 1. Skipping this is
   the single most common reason sends "succeed" but nothing arrives.

### 3. Firebase — a service account for the server

1. **Project settings → Service accounts → Generate new private key**.
2. That JSON is the value of `PAI_FCM_CREDENTIALS`.

### 4. Xcode — the Push Notifications capability

Open `client/ios/Runner.xcworkspace`, select the **Runner** target →
**Signing & Capabilities** → **+ Capability** → **Push Notifications**. This
points the target's `CODE_SIGN_ENTITLEMENTS` at the `Runner.entitlements` file
already committed here. Add **Background Modes** and tick **Remote
notifications** too (`Info.plist` already declares it; the capability makes
Xcode agree).

Then:

```bash
cd client && flutter pub get && cd ios && pod install
```

### 5. Server environment variables

Set these on Vercel (**Settings → Environment Variables**) and in `.env` for
local runs:

| Variable | Value |
| --- | --- |
| `PAI_FCM_PROJECT_ID` | Firebase project id, e.g. `pai-client-4f2a1` |
| `PAI_FCM_CREDENTIALS` | The whole service-account JSON, pasted as one line |
| `CRON_SECRET` | Only if you use Vercel Cron — Vercel sends this as the bearer token |

With neither FCM variable set, `PushSender` reports `enabled=False` and every
send becomes a no-op. Nothing errors; notifications just never arrive. The cron
response says so explicitly in `push_enabled` / `push_disabled_reason`.

### 6. Database

```bash
alembic upgrade head
```

Adds `device_tokens` and `nudge_deliveries`.

## Choosing a scheduler

`vercel.json` currently has one daily cron at `0 14 * * *` (19:30 IST), which
covers the evening rules only. Two limits matter:

- **Vercel Cron issues GET only.** `/cron/nudges` now accepts both GET and POST;
  before this change it was POST-only and every scheduled run was a 405.
- **Vercel Hobby allows one cron job per project**, so the morning brief and the
  evening check cannot both run there.

`.github/workflows/nudges.yml` is committed as the alternative: GitHub Actions
has no schedule limit, is free, and already fires at both 08:10 and 19:40 IST.
Add `PAI_BASE_URL` and `PAI_API_KEY` as repository secrets to enable it, and
delete the `crons` block from `vercel.json` so the two do not overlap. (If both
run, nothing breaks — the delivery ledger deduplicates — but the logs get
confusing.)

## What gets pushed, and what does not

A notification the user did not need is annoying in a way that compounds, so
each rule declares a channel.

| Rule | Channel | Why |
| --- | --- | --- |
| `overdue_tasks` | **push** | Actionable right now |
| `protein_behind` | **push** | Fires at 19:30 — still time to eat |
| `morning_brief` | **push** | One predictable ping while the day is plannable |
| `nothing_logged` | **push** | The only rule that fires on *absence* of data |
| `weekly_review` | **push** | Sunday evening, once a week |
| `calories_over` | in-app | Retrospective — you cannot un-eat it |
| `water_behind` | in-app | True most evenings; pushing it trains you to swipe |
| `workout_gap` | in-app | The morning brief raises it at a useful hour instead |

`GET /nudges` still returns **everything**, push and in-app alike, so nothing
was removed from the app — only from the lock screen.

On top of the channel split, `select_for_push` enforces:

- **Quiet hours** 22:00–07:00 IST. A nudge that would land in the window is
  dropped, not queued — by morning it is stale and the next tick re-derives it.
- **A cap of 2 pushes per rolling 24h.** `evaluate()` sorts by priority first,
  so the most urgent survive.
- **A per-kind dedup window**, 18h by default and 6 days for the weekly review.
  Only a *delivered* nudge opens the window, so a Firebase outage cannot
  silence tomorrow.

Tuning lives at the top of `src/domains/review/nudges.py`.

## Verifying it works

The app's **Send a test notification** button now exercises the *local*
path, not this one — the FCM test helper was removed from the client when
remote push went dormant. Test this path from a terminal instead:

```bash
curl -X POST https://your-app.vercel.app/devices/test \
  -H "Authorization: Bearer $PAI_API_KEY"
```

To see what the scheduler would do without waiting for it:

```bash
curl https://your-app.vercel.app/cron/nudges \
  -H "Authorization: Bearer $PAI_API_KEY"
```

The response names every nudge and, for each one not sent, why:
`in_app_only`, `quiet_hours`, `recently_sent`, or `daily_cap`.

## When nothing arrives

Work down this list — it is ordered by how often each one is the cause.

| Symptom | Cause |
| --- | --- |
| `"push_enabled": false` | `PAI_FCM_PROJECT_ID` / `PAI_FCM_CREDENTIALS` missing on the server |
| `"devices": 0` | The app never registered — sign out and back in; registration happens on sign-in |
| `sent > 0` but no notification on the phone | APNs key not uploaded to Firebase (step 2.4), or a release build carrying a `development` entitlement |
| `tokens_pruned > 0` every run | The app was uninstalled, or the bundle ID in Firebase does not match the build |
| Works in debug, not from TestFlight | `aps-environment` must be `production`; enabling the Xcode capability handles this |
| Nothing at all on the Simulator | iOS Simulator cannot receive remote push. Use a physical device |
| `"suppressed": {"x": "quiet_hours"}` | Working as intended — 22:00–07:00 IST |

## A known quirk worth knowing

`aggregate.today_snapshot` bounds "today" in **UTC**, while the nudge rules
judge the hour in **IST**. So the evening rules read a day window that started
at 05:30 IST, and anything eaten between midnight and 05:30 counts toward the
previous day. It predates this work and is left alone deliberately, but it does
slightly skew `protein_behind` and `nothing_logged`. Fixing it means switching
`_day_bounds` to `LOCAL_TZ`, which changes what every existing row aggregates
into — worth doing on purpose, not as a side effect.
