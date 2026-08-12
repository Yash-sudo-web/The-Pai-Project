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

## The Back Tap fallback

iOS cannot bind a gesture directly to an app action, so the app registers the
`pai://` URL scheme and opening **`pai://listen`** starts a turn — the same
loop, minus the phrase.

To wire it up:

1. **Shortcuts** app → new shortcut → **Open URL** → `pai://listen`. Name it
   something like "Ask Pai".
2. **Settings → Accessibility → Touch → Back Tap → Double Tap** → pick that
   shortcut. On a Pro, the **Action Button** works too.

Double-tap the back of the phone and it starts listening. Costs no battery,
never false-triggers, and works when wake-word detection is switched off or has
quietly died — which is exactly why it exists.

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
also stops the detector, and why a mic-button tap is ignored mid-turn.

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
| App Store | `audio` background mode used for listening rather than playback is a rejection reason. Fine while sideloading. |

Every row below the first is a reason the Back Tap route is worth wiring up.

Windows is unaffected — `WakeWordService.supported` is false there, the
provider gets a null detector, and `listenAndTranscribe` refuses early. The
desktop build was verified to still compile.
