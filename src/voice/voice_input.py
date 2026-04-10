"""Voice input module with push-to-talk and wake-word activation."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from src.types import AuditEntry, DomainName
from src.voice.transcription import TranscriptionEngine, TranscriptionError


class VoiceInputError(RuntimeError):
    """Raised when voice capture infrastructure is misconfigured."""


class SoundDeviceRecorder:
    """Audio recorder backed by `sounddevice` for real microphone capture."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        silence_threshold: float = 0.01,
        silence_duration: float = 1.0,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._silence_threshold = silence_threshold
        self._silence_duration = silence_duration
        self._frames: list[bytes] = []
        self._stream = None

    def start_recording(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional runtime path
            raise VoiceInputError("sounddevice is required for live recording") from exc

        self._frames = []

        def _callback(indata, frames, time_info, status):  # pragma: no cover - exercised only with real audio
            _ = frames, time_info, status
            self._frames.append(indata.copy().tobytes())

        self._stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            callback=_callback,
        )
        self._stream.start()

    def stop_recording(self) -> bytes:
        if self._stream is None:
            raise VoiceInputError("Recording has not been started")

        self._stream.stop()
        self._stream.close()
        self._stream = None
        return self._frames_to_wav_bytes(self._frames)

    def record_until_silence(self) -> bytes:
        self.start_recording()
        try:
            import audioop
            import time

            silent_for = 0.0
            previous_frame_count = 0
            while True:
                time.sleep(0.1)
                if len(self._frames) == previous_frame_count:
                    continue
                latest = self._frames[-1]
                previous_frame_count = len(self._frames)
                rms = audioop.rms(latest, 2) / 32768.0
                if rms < self._silence_threshold:
                    silent_for += 0.1
                else:
                    silent_for = 0.0
                if silent_for >= self._silence_duration:
                    break
            return self.stop_recording()
        except Exception:
            with contextlib.suppress(Exception):
                self.stop_recording()
            raise

    def _frames_to_wav_bytes(self, frames: list[bytes]) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            path = handle.name
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(self._channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(b"".join(frames))
        with open(path, "rb") as saved:
            return saved.read()


class PynputPushToTalkHotkey:
    """Keyboard hotkey listener implemented with `pynput`."""

    def __init__(self, key: str, on_press: Callable[[], Any], on_release: Callable[[], Any]) -> None:
        self._key = key.lower()
        self._on_press = on_press
        self._on_release = on_release
        self._listener = None
        self._pressed = False

    def start(self) -> None:
        try:
            from pynput import keyboard  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional runtime path
            raise VoiceInputError("pynput is required for push-to-talk hotkeys") from exc

        target_key = self._key

        def _normalize(key_obj) -> str | None:  # pragma: no cover - exercised via injected fakes in tests
            if hasattr(key_obj, "char") and key_obj.char:
                return str(key_obj.char).lower()
            if hasattr(key_obj, "name") and key_obj.name:
                return str(key_obj.name).lower()
            return None

        def _on_press(key_obj):
            normalized = _normalize(key_obj)
            if normalized != target_key or self._pressed:
                return
            self._pressed = True
            self._on_press()

        def _on_release(key_obj):
            normalized = _normalize(key_obj)
            if normalized != target_key or not self._pressed:
                return
            self._pressed = False
            self._on_release()

        self._listener = keyboard.Listener(on_press=_on_press, on_release=_on_release)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._pressed = False


class OpenWakeWordDetector:
    """Wake-word detector wrapper using `openwakeword`."""

    def __init__(
        self,
        model_path: str | None = None,
        audio_source: Any | None = None,
        chunk_size: int = 1280,
        sample_rate: int = 16000,
        threshold: float = 0.5,
    ) -> None:
        try:
            from openwakeword.model import Model  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional runtime path
            raise VoiceInputError("openwakeword is required for wake-word detection") from exc

        kwargs = {"wakeword_models": [model_path]} if model_path else {}
        self._model = Model(**kwargs)
        self._audio_source = audio_source
        self._chunk_size = chunk_size
        self._sample_rate = sample_rate
        self._threshold = threshold

    def detect(self) -> bool:
        chunk = self._read_audio_chunk()
        prediction = self._model.predict(chunk)
        if isinstance(prediction, dict):
            return any(score > self._threshold for score in prediction.values())
        return bool(prediction)

    def _read_audio_chunk(self):
        if self._audio_source is not None:
            return self._audio_source.read()
        try:
            import sounddevice as sd  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional runtime path
            raise VoiceInputError("sounddevice is required for wake-word detection") from exc
        chunk = sd.rec(
            self._chunk_size,
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            blocking=True,
        )
        return chunk.flatten()


@dataclass
class VoiceActivationConfig:
    push_to_talk_key: str = "f8"
    wake_word_poll_interval: float = 0.1


class VoiceInputModule:
    """Captures audio, transcribes it, and forwards text to the orchestrator."""

    def __init__(
        self,
        transcription_engine: TranscriptionEngine,
        on_transcription: Callable[[str], Any],
        on_error: Callable[[str], Any] | None = None,
        activation_mode: str = "push_to_talk",
        wake_word_detector: Any | None = None,
        recorder: Any | None = None,
        hotkey_listener: Any | None = None,
        audit_log: Any | None = None,
        config: VoiceActivationConfig | None = None,
    ) -> None:
        self.activation_mode = activation_mode
        self._transcription_engine = transcription_engine
        self._on_transcription = on_transcription
        self._on_error = on_error or (lambda message: None)
        self._wake_word_detector = wake_word_detector
        self._recorder = recorder or SoundDeviceRecorder()
        self._hotkey_listener = hotkey_listener
        self._audit_log = audit_log
        self._config = config or VoiceActivationConfig()
        self._listening = False
        self._running = False
        self._wake_task: asyncio.Task[None] | None = None

    def start_listening(self) -> None:
        self._listening = True

    def stop_listening(self) -> None:
        self._listening = False

    def start(self) -> None:
        """Start background activation infrastructure for the configured mode."""
        if self._running:
            return

        self._running = True
        if self.activation_mode == "push_to_talk":
            if self._hotkey_listener is None:
                self._hotkey_listener = PynputPushToTalkHotkey(
                    key=self._config.push_to_talk_key,
                    on_press=self._begin_push_to_talk_capture,
                    on_release=self._end_push_to_talk_capture,
                )
            self._hotkey_listener.start()
            return

        if self.activation_mode == "wake_word":
            if self._wake_word_detector is None:
                self._wake_word_detector = OpenWakeWordDetector()
            loop = asyncio.get_running_loop()
            self._wake_task = loop.create_task(self._wake_word_loop())
            return

        raise VoiceInputError(f"Unsupported activation mode: {self.activation_mode}")

    async def stop(self) -> None:
        """Stop any active background listeners."""
        self._running = False
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()

        if self._wake_task is not None:
            self._wake_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._wake_task
            self._wake_task = None

        self.stop_listening()

    def handle_audio(self, audio_bytes: bytes) -> str | None:
        """Transcribe audio and dispatch the resulting command."""
        try:
            text = self._transcription_engine.transcribe(audio_bytes)
        except TranscriptionError as exc:
            self._log_voice_failure(str(exc))
            self._on_error(str(exc))
            return None

        self._on_transcription(text)
        return text

    def process_push_to_talk(self, audio_bytes: bytes) -> str | None:
        self.start_listening()
        try:
            return self.handle_audio(audio_bytes)
        finally:
            self.stop_listening()

    def _begin_push_to_talk_capture(self) -> None:
        self.start_listening()
        self._recorder.start_recording()

    def _end_push_to_talk_capture(self) -> str | None:
        try:
            audio_bytes = self._recorder.stop_recording()
            return self.handle_audio(audio_bytes)
        finally:
            self.stop_listening()

    async def check_wake_word_once(self) -> bool:
        """Poll the wake-word detector once for tests and simple loops."""
        if self._wake_word_detector is None:
            return False
        detected = bool(self._wake_word_detector.detect())
        if not detected:
            return False

        self.start_listening()
        try:
            audio_bytes = self._recorder.record_until_silence()
            return self.handle_audio(audio_bytes) is not None
        finally:
            self.stop_listening()

    async def _wake_word_loop(self) -> None:
        while self._running:
            await self.check_wake_word_once()
            await asyncio.sleep(self._config.wake_word_poll_interval)

    def _log_voice_failure(self, message: str) -> None:
        if self._audit_log is None:
            return
        entry = AuditEntry(
            id=datetime.now(tz=timezone.utc).isoformat(),
            timestamp=datetime.now(tz=timezone.utc),
            tool_name="voice.transcription",
            domain=DomainName.system_control,
            inputs={"activation_mode": self.activation_mode},
            output=None,
            error=message,
            approval_status="not_required",
            session_id="voice",
        )
        self._audit_log.record(entry)
