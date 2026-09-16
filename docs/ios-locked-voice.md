# iPhone 14 Pro Max: locked-screen voice discovery

## Target experience

1. Start **Ask Pai** while the phone is unlocked, then lock it. Pai finishes
   recording the request, gets the answer, and speaks it aloud with the screen
   locked.
2. While a turn is active, show **Listening**, **Thinking**, and **Speaking**
   in a Live Activity. On an unlocked iPhone 14 Pro Max this appears in the
   Dynamic Island; on the Lock Screen it appears in the Live Activity area.
3. End the activity and release the audio session when the turn ends. Never
   leave an idle activity implying that the microphone is on.

## Platform boundary

- `AVAudioSession.Category.playAndRecord` supports input and output with the
  screen locked. `UIBackgroundModes = audio` is also required. The app declares
  the background mode and now configures the category for a hands-free turn.
- Background recording is a **continuation** of a session begun while the app
  was active. Apple's developer support says general audio APIs cannot activate
  a fresh recording session from a fully backgrounded app. A Lock Screen
  control, Back Tap, Siri, or an App Intent must be tested as a user-initiated
  launch path; do not promise that a cold, locked launch can record without
  unlocking.
- A Live Activity displays state; it is not a mechanism that keeps the app
  running. Use a normal Live Activity, not the transient style, which ends on
  lock. It requires an iOS widget extension and `NSSupportsLiveActivities`.
- Apple owns the orange microphone indicator. Pai can add its own Live Activity
  but cannot repurpose that indicator or force the Dynamic Island to stay
  visible while locked.

## Implementation and verification

| Area | Built in this change | Device verification / remaining work |
| --- | --- | --- |
| Voice pipeline | A finite background task starts before recording stops and ends after the spoken reply; cancellation closes the audio session | Confirm the network work finishes before iOS expires that task; handle very slow requests more explicitly |
| Audio session | The iOS runner sets `playAndRecord`; `audioplayers` uses the same category; the runner observes interruptions | Test speaker, Ring/Silent, AirPods, calls, Siri, and route changes on the device |
| Recording | `SttService` uses `record` and a 150 ms amplitude stream for endpointing, with cancellation support | Verify amplitude callbacks and stop/transcribe complete after locking |
| Playback | `TtsService` uses `audioplayers` and can now be stopped mid-reply | Verify locked playback; report synthesis/playback failure rather than showing **Speaking** falsely |
| UI | **Ask Pai** in the iPhone chat reports the Live Activity start result. The ActivityKit widget shows Listening → Transcribing → Thinking → Speaking, then Done briefly on the Lock Screen; Stop links to `pai://stop` | Check Live Activity permission and layout on the actual Lock Screen and Dynamic Island; Stop may require unlock |
| Follow-up | With wake word disabled, the loop stops after one answer | Decide whether a short, explicitly user-started conversation should allow a bounded follow-up while locked |

## Next build order

1. Test the turn on a physical iPhone 14 Pro Max, with the wake detector off.
   Resolve any native compile or signing issues in Xcode. Apple's background
   task time is limited, so a slow network request may still expire.
2. Add a native App Shortcut / control if a quicker launch is needed after the
   core locked-turn path works. Determine on-device whether it can begin a turn
   while locked or whether Face ID/passcode is required.
3. Measure battery drain and completion rate with the wake detector off. Test
   Wi-Fi/cellular loss, slow API calls, Silent mode, AirPods, a phone call/Siri
   interruption, and force quit/reboot separately.

## Acceptance checks on the physical phone

- Start Pai, speak, press the Side button to lock before speech ends: recording
  finishes and a spoken answer is heard without unlocking.
- Lock after recording but during network work: the answer still arrives or the
  turn ends with a clear error and no stale Live Activity.
- The Live Activity shows the correct phase on the Lock Screen, and the Dynamic
  Island shows it when unlocked. Stop releases the microphone promptly. If no
  activity appears, read the status beneath **Ask Pai** for the native failure
  reason or a missing widget extension.
- Start from Back Tap, Siri, and any Lock Screen control while already locked:
  record whether iOS requires unlock. Do not treat an unlock prompt as a bug in
  Pai.
- With Pai idle, the orange microphone indicator is absent and the Live
  Activity is gone.

## Apple sources

- [Background audio modes](https://developer.apple.com/documentation/xcode/configuring-background-execution-modes)
- [`playAndRecord` with the screen locked](https://developer.apple.com/documentation/avfaudio/avaudiosession/category-swift.struct/playandrecord)
- [Finite background task](https://developer.apple.com/documentation/uikit/extending-your-app-s-background-execution-time)
- [Live Activities and their Lock Screen/Dynamic Island presentations](https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities)
- [Audio recording intent and Live Activity requirement](https://developer.apple.com/documentation/appintents/audiorecordingintent)
- [Apple developer support on starting background recording](https://developer.apple.com/forums/thread/816408)
- [System microphone indicator](https://support.apple.com/108331)
