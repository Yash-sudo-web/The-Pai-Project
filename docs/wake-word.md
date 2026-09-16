# Wake word

Say **"Hey Pai"**, ask a question, hear the answer — no tapping.

## The turn

```
wakeListening ──"Hey Pai"──► recording ──1.5s silence──► transcribing
      ▲                        ▲                            │
      │                        │ 5s follow-up window    auto-send
      │                        │                            ▼
      └──── nothing said ──── speaking ◄──── TTS ◄──── /command/stream
```

After a reply the microphone stays open for **5 seconds**, so "and my calories?"
needs no second wake word. Say nothing and it closes and re-arms itself.

## The engine

[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) keyword spotting —
Apache-2.0, fully offline, **no account, no key, no trial**. The model is
`sherpa-onnx-kws-zipformer-gigaspeech-3.3M` (English, GigaSpeech 10k hours),
bundled at `client/assets/kws/` as int8 ONNX: **~5 MB** total.

Porcupine was the first choice and was abandoned — Picovoice now requires a
business email and grants only a 7-day trial, which is no basis for something
meant to run indefinitely.

### Changing the phrase

The model is not trained on the wake word. It is a tiny streaming recogniser
whose decoding is constrained to a keyword list, so a new phrase is a text
change, not a training run:

```bash
pip install sentencepiece          # build-time only
python client/tool/make_keywords.py "HEY PAI"
```

That writes BPE tokens to `client/assets/kws/keywords.txt`:

```
▁HE Y ▁PA I
```

Rebuild and the new phrase is live. Multiple phrases: one per line.

Keep them **upper-case** and at least three or four tokens — short phrases
false-trigger badly. The script warns when a phrase is too short.

### Tuning sensitivity

Two constants at the top of `WakeWordService`:

| Constant | Default | Effect |
| --- | --- | --- |
| `_keywordsThreshold` | 0.25 | Lower catches the phrase more often, and more things that aren't it |
| `_keywordsScore` | 1.0 | Boosts the keyword against ordinary speech |

Per-phrase overrides go in `keywords.txt` as `:score` and `#threshold` suffixes.

These defaults are the model's, not measurements from your voice — expect to
move them.

## On-demand iPhone launch (recommended for battery life)

iOS Shortcuts can open the app's `pai://listen` URL. That starts one hands-free
turn without leaving the wake-word detector active between requests. Turn off
**Settings → Wake word → Listen for "Hey Pai"** to release the microphone when
idle. With this setting off, the app listens for one request and ends after its
reply; the five-second follow-up window is only used when wake-word listening
is enabled.

The iPhone chat screen also has an **Ask Pai** button for the same spoken turn.
The round mic button still records a draft transcript for editing before send.

To wire it up:

1. **Shortcuts** app → new shortcut → **Open URL** → `pai://listen`. Name it
   something like "Ask Pai".
2. **Settings → Accessibility → Touch → Back Tap → Double Tap** (or Triple Tap)
   → pick that shortcut.

Other ways to run the same shortcut on an iPhone 14 Pro Max:

| Trigger | Setup | Tradeoff |
| --- | --- | --- |
| Lock Screen control (iOS 18.2+) | Customize the Lock Screen and look for a Shortcut control; select **Ask Pai** if offered | Visible, one touch; availability of this URL-based shortcut needs checking on-device, and opening the app may require unlock |
| Control Center (iOS 18+) | Add a **Shortcut** control and choose **Ask Pai** | Available from many screens; takes a swipe and tap |
| Side button + Siri | Press and hold the Side button, then say **Ask Pai** | Physical button, but Siri must recognize the shortcut name; opening the app from a locked phone may require unlock |
| Voice Control custom command | Settings → Accessibility → Voice Control → Customize Commands → Create New Command → Run Shortcut | Needs Voice Control listening, so it does not solve the always-listening concern |
| Vocal Shortcuts | Settings → Accessibility → Vocal Shortcuts | Also uses the microphone continuously to detect the phrase |

The iPhone 14 Pro Max has a Ring/Silent switch, **not an Action button**; the
Side button cannot be directly assigned to an arbitrary Shortcut. Back Tap is
the simplest low-idle-power trigger on this model. A trigger can fail or fire
accidentally, so test it on the physical phone, both unlocked and locked.

Apple references: [Back Tap](https://support.apple.com/guide/shortcuts/apd897693606/ios),
[Control Center Shortcuts](https://support.apple.com/guide/shortcuts/apd06a9201d4/ios),
[Lock Screen App Shortcuts](https://support.apple.com/121131),
[Siri Shortcuts](https://support.apple.com/guide/shortcuts/apd07c25bb38/ios),
[Vocal Shortcuts](https://support.apple.com/guide/iphone/iph7f242ea2c/ios),
[iPhone 14 Pro Max hardware](https://support.apple.com/guide/iphone/iphed34f9f10/ios).

## Setup

1. Build to a physical iPhone. The Simulator has no usable microphone path.
2. **Settings → Wake word → Listen for "Hey Pai"** → on. Accept the microphone
   prompt; refusing turns the toggle back off with the reason shown.
3. Optionally wire the Back Tap shortcut above.

Nothing else — no accounts, no keys, no model download.

## Design notes

**Only one thing can hold the microphone.** The detector streams from it
continuously and `SttService` needs it to record your question, so every
transition stops one before starting the other. That is why `toggleRecording`
also stops the detector; during a hands-free turn, the mic button stops it.

**It must not hear itself.** Detection is off while the assistant speaks, and
the reply is played with `speakAndWait` so the microphone reopens only once
playback has genuinely finished. The cost is no barge-in.

**Audio path.** `record` streams little-endian PCM16 at 16 kHz; the service
converts to normalised floats and feeds `acceptWaveform`. The sample rate is
not negotiable — the model is trained at 16 kHz and anything else degrades
detection silently rather than erroring.

**Endpointing is a guess, and it is the part to tune.** Speech is detected
above **-35 dBFS**, an utterance ends after **1.5s** below it, capped at
**15s**. Before speech starts the patience is shorter — 3s after the wake word,
5s in a follow-up window — after which the turn is abandoned rather than
recording an empty room. Set from first principles, not from testing on your
voice.

**Two voice paths, one pipeline.** The mic button still drops its transcript
into the input box to edit. Only the wake path and `pai://listen` auto-submit.
`ChatProvider._handsFree` is the flag that separates them.

## Known limits

| Limit | Effect |
| --- | --- |
| **Battery** | A streaming transducer running continuously is heavier than a purpose-built wake-word net. This is the feature's dominant cost and the first thing to measure over a real day. |
| Audio session interruptions | A call, Siri, or another app taking the mic stops detection. Reopening the app re-arms it. |
| Termination without restart | iOS may kill the app under memory pressure; nothing brings it back. |
| Reboots | Nothing runs until you open the app manually. |
| 7-day sideload expiry | The app stops launching until re-signed. |
| App Store | Background audio supports a user-started recording or playback session, but using it solely to keep a passive wake-word detector alive has App Review risk. |

Every row below the first is a reason the Back Tap route is worth wiring up.

Windows is unaffected — `WakeWordService.supported` is false there, the
provider gets a null detector, and `listenAndTranscribe` refuses early. The
desktop build was verified to still compile.
