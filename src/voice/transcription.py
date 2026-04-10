"""Configurable transcription engine for voice input."""

from __future__ import annotations

import io
import logging
import os
import tempfile
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Raised when transcription fails."""


class TranscriptionEngine:
    """Transcribe audio bytes using a local Whisper model or OpenAI API."""

    def __init__(
        self,
        backend: str | None = None,
        local_model: Any | None = None,
        openai_client: Any | None = None,
    ) -> None:
        self._backend = backend or os.environ.get("PAI_TRANSCRIPTION_BACKEND", "groq")
        self._local_model = local_model
        self._openai_client = openai_client

    def transcribe(self, audio_bytes: bytes) -> str:
        started = time.perf_counter()
        try:
            if self._backend == "api":
                text = self._transcribe_api(audio_bytes)
            elif self._backend == "groq":
                text = self._transcribe_groq(audio_bytes)
            else:
                text = self._transcribe_local(audio_bytes)
        except Exception as exc:
            raise TranscriptionError(str(exc)) from exc

        if time.perf_counter() - started > 5.0:
            logger.warning("Voice transcription exceeded 5 seconds")

        cleaned = text.strip()
        if not cleaned:
            raise TranscriptionError("Empty or unintelligible transcription")
        return cleaned

    def _transcribe_local(self, audio_bytes: bytes) -> str:
        model = self._local_model
        if model is None:
            try:
                import whisper  # type: ignore
            except ImportError as exc:  # pragma: no cover - optional runtime path
                raise TranscriptionError("Local Whisper backend is unavailable") from exc
            model = whisper.load_model(os.environ.get("PAI_WHISPER_MODEL", "base"))

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(audio_bytes)
            path = handle.name
        result = model.transcribe(path)
        return str(result.get("text", ""))

    def _transcribe_api(self, audio_bytes: bytes) -> str:
        client = self._openai_client
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise TranscriptionError("OpenAI client is unavailable") from exc
            client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "command.wav"
        response = client.audio.transcriptions.create(
            model=os.environ.get("PAI_WHISPER_API_MODEL", "whisper-1"),
            file=audio_file,
        )
        return str(getattr(response, "text", ""))

    def _transcribe_groq(self, audio_bytes: bytes) -> str:
        client = self._openai_client
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise TranscriptionError("OpenAI-compatible client is unavailable") from exc
            client = OpenAI(
                api_key=os.environ.get("GROQ_API_KEY"),
                base_url=os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            )

        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = "command.wav"
        response = client.audio.transcriptions.create(
            model=os.environ.get("GROQ_TRANSCRIPTION_MODEL", "whisper-large-v3-turbo"),
            file=audio_file,
            language=os.environ.get("GROQ_TRANSCRIPTION_LANGUAGE") or None,
            response_format="json",
            temperature=0.0,
        )
        return str(getattr(response, "text", ""))
