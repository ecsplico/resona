"""
Live transcription engine with VAD-based chunking and local agreement.

This module provides a stateful transcriber for real-time audio streams.
Each WebSocket session or TUI instance creates its own LiveTranscriber.

Key features:
- Rolling audio buffer with min/max duration control
- Uses faster-whisper's built-in Silero VAD to skip silence
- Local agreement: compares overlapping transcriptions to find stable prefix
- Stale-cycle recovery: force-confirms text after repeated agreement failures
- Capped transcription window to prevent quality degradation on long buffers
- Thread-safe: transcription runs in a thread pool executor
"""
import logging
import asyncio
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import threading
from threading import Lock
from typing import Optional, NamedTuple

from resona_asr_core.registry import get_transcriber

log = logging.getLogger(__name__)

# Audio config
SAMPLE_RATE = 16000

# Chunking config
MIN_CHUNK_SECONDS = 3
MAX_BUFFER_SECONDS = 30.0
MAX_TRANSCRIBE_SECONDS = 15.0
OVERLAP_SECONDS = 1.0
MIN_NEW_AUDIO_SECONDS = 0.5

# Recovery config
MAX_STALE_CYCLES = 4


class TranscriptionResult(NamedTuple):
    """Result of a live transcription step."""
    confirmed: str
    partial: str
    language: str
    confirmed_delta: str = ""


# Shared thread pool for transcription (avoids blocking asyncio loop)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="live-asr")


class LiveTranscriber:
    """Stateful live transcriber for a single audio stream/session."""

    def __init__(self, language: str = "de", task: str = "transcribe"):
        self.language = language
        self.task = task
        self.buffer = np.array([], dtype=np.float32)
        self._lock = Lock()
        self._transcriber = None
        self._prev_text = ""
        self._confirmed_text = ""
        self._emitted_word_count = 0
        self._audio_event: asyncio.Event = asyncio.Event()
        self._audio_event_sync: threading.Event = threading.Event()
        self._last_processed_buffer_end: float = 0.0
        self._stale_cycles: int = 0

    def _get_transcriber(self):
        """Lazy-load the transcriber (expensive model init)."""
        if self._transcriber is None:
            self._transcriber = get_transcriber()
        return self._transcriber

    def add_audio(self, audio: np.ndarray) -> None:
        """Add audio samples to the buffer. Thread-safe."""
        with self._lock:
            self.buffer = np.concatenate([self.buffer, audio])
            max_samples = int(MAX_BUFFER_SECONDS * SAMPLE_RATE)
            if len(self.buffer) > max_samples:
                overflow = len(self.buffer) - max_samples
                self.buffer = self.buffer[-max_samples:]
                self._prev_text = ""
                self._emitted_word_count = 0
                self._last_processed_buffer_end = 0.0
                log.debug(f"Buffer capped at {MAX_BUFFER_SECONDS}s, dropped {overflow / SAMPLE_RATE:.1f}s, state reset")

            current_end = len(self.buffer) / SAMPLE_RATE
            if current_end - self._last_processed_buffer_end >= MIN_NEW_AUDIO_SECONDS:
                self._audio_event.set()
                self._audio_event_sync.set()

    def buffer_duration(self) -> float:
        """Current buffer duration in seconds."""
        return len(self.buffer) / SAMPLE_RATE

    def has_enough_audio(self) -> bool:
        """Check if buffer has enough audio for transcription."""
        return self.buffer_duration() >= MIN_CHUNK_SECONDS

    def _transcribe_sync(self, audio: np.ndarray) -> dict:
        """Run transcription synchronously (called in thread pool)."""
        transcriber = self._get_transcriber()
        return transcriber.transcribe(
            audio,
            task=self.task,
            language=self.language,
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=1000,
                speech_pad_ms=400,
            ),
            condition_on_previous_text=False,
            initial_prompt=" ",
            word_timestamps=True,
        )

    @staticmethod
    def _normalize_word(word: str) -> str:
        """Normalize word for comparison (lower, strip punctuation)."""
        return word.lower().strip(" .,?!:;\"'")

    @staticmethod
    def _find_stable_word_count(prev_words: list[str], curr_words: list[str]) -> int:
        """Find the number of common prefix words between two transcriptions."""
        norm = LiveTranscriber._normalize_word

        common_len = 0
        for pw, cw in zip(prev_words, curr_words):
            if norm(pw) == norm(cw):
                common_len += 1
            else:
                break
        if common_len > 0:
            return common_len

        best = 0
        for skip_prev, skip_curr in [(1, 0), (0, 1), (1, 1)]:
            if skip_prev >= len(prev_words) or skip_curr >= len(curr_words):
                continue
            run = 0
            for pw, cw in zip(prev_words[skip_prev:], curr_words[skip_curr:]):
                if norm(pw) == norm(cw):
                    run += 1
                else:
                    break
            if run >= 2:
                candidate = skip_curr + run
                best = max(best, candidate)
        return best

    def _process_result(self, result: dict) -> Optional[TranscriptionResult]:
        """Shared logic for processing a transcription result dict."""
        segments = result.get("segments", [])
        all_words = []
        for seg in segments:
            if hasattr(seg, 'words') and seg.words:
                all_words.extend(seg.words)

        curr_words_str = [w.word.strip() for w in all_words]

        if not all_words:
            curr_text = result.get("text", "").strip()
            curr_words_str = curr_text.split()
        else:
            curr_text = " ".join(curr_words_str)

        if curr_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
            curr_text = ""
            curr_words_str = []
            all_words = []

        if not curr_text:
            return None

        prev_words_str = self._prev_text.split()

        confirmed_count = self._find_stable_word_count(prev_words_str, curr_words_str)

        new_confirmed_this_cycle = confirmed_count > self._emitted_word_count
        if not new_confirmed_this_cycle and curr_words_str:
            self._stale_cycles += 1
        else:
            self._stale_cycles = 0

        if self._stale_cycles >= MAX_STALE_CYCLES and len(curr_words_str) >= 2:
            log.info(f"Stale-cycle recovery: force-confirming {len(curr_words_str)} words after {self._stale_cycles} stale cycles")
            confirmed_count = len(curr_words_str)
            self._stale_cycles = 0

        confirmed_words_objs = all_words[:confirmed_count] if all_words else []
        confirmed_words = curr_words_str[:confirmed_count]
        partial_words = curr_words_str[confirmed_count:]

        partial_str = " ".join(partial_words)

        text_to_emit = " ".join(confirmed_words[self._emitted_word_count:])
        self._emitted_word_count = max(self._emitted_word_count, len(confirmed_words))

        if text_to_emit:
            if self._confirmed_text:
                self._confirmed_text += " " + text_to_emit
            else:
                self._confirmed_text = text_to_emit

        if confirmed_words_objs:
            last_confirmed_word = confirmed_words_objs[-1]
            t_end = last_confirmed_word.end

            t_target = max(0.0, t_end - 10.0)

            t_actual_cut = 0.0
            for w in all_words:
                if w.end > t_target:
                    t_actual_cut = w.start
                    break

            cut_samples = int(t_actual_cut * SAMPLE_RATE)

            with self._lock:
                if cut_samples < len(self.buffer):
                    self.buffer = self.buffer[cut_samples:]
                else:
                    self.buffer = np.array([], dtype=np.float32)

            retained_confirmed = [w.word.strip() for w in confirmed_words_objs if w.start >= t_actual_cut]
            retained_all = [w.word.strip() for w in all_words if w.start >= t_actual_cut]
            self._prev_text = " ".join(retained_all)
            self._emitted_word_count = len(retained_confirmed)

        else:
            self._prev_text = curr_text

        return TranscriptionResult(
            confirmed=self._confirmed_text,
            partial=partial_str,
            language=result.get("language", self.language),
            confirmed_delta=text_to_emit,
        )

    def _get_transcription_audio(self) -> np.ndarray:
        """Get the audio chunk to transcribe, capped to MAX_TRANSCRIBE_SECONDS."""
        with self._lock:
            self._last_processed_buffer_end = len(self.buffer) / SAMPLE_RATE
            max_samples = int(MAX_TRANSCRIBE_SECONDS * SAMPLE_RATE)
            if len(self.buffer) > max_samples:
                audio_chunk = self.buffer[-max_samples:].copy()
            else:
                audio_chunk = self.buffer.copy()
        return audio_chunk

    # ── Synchronous entry-points (for background threads) ────────────

    def process_sync(self) -> Optional[TranscriptionResult]:
        """Process current buffer synchronously. Call from a background thread."""
        if not self.has_enough_audio():
            return None

        audio_chunk = self._get_transcription_audio()

        try:
            result = self._transcribe_sync(audio_chunk)
        except Exception as e:
            log.error(f"Live transcription error: {e}", exc_info=True)
            return None

        return self._process_result(result)

    def flush_sync(self) -> Optional[TranscriptionResult]:
        """Process remaining buffer synchronously. Call from a background thread."""
        with self._lock:
            if len(self.buffer) < int(SAMPLE_RATE * 0.1):
                return TranscriptionResult(
                    confirmed=self._confirmed_text,
                    partial="",
                    language=self.language,
                    confirmed_delta="",
                )
            audio_chunk = self.buffer.copy()
            self.buffer = np.array([], dtype=np.float32)

        try:
            result = self._transcribe_sync(audio_chunk)
        except Exception as e:
            log.error(f"Live transcription flush error: {e}", exc_info=True)
            return None

        segments = result.get("segments", [])
        all_words = []
        for seg in segments:
            if hasattr(seg, 'words') and seg.words:
                all_words.extend(seg.words)

        if all_words:
            flush_words = [w.word.strip() for w in all_words]
        else:
            raw = result.get("text", "").strip()
            flush_words = raw.split() if raw else []

        language = result.get("language", self.language)

        flush_text = " ".join(flush_words)
        if flush_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
            flush_words = []

        new_words = flush_words[self._emitted_word_count:]
        text_to_emit = " ".join(new_words)

        if text_to_emit:
            if self._confirmed_text:
                self._confirmed_text += " " + text_to_emit
            else:
                self._confirmed_text = text_to_emit

        return TranscriptionResult(
            confirmed=self._confirmed_text,
            partial="",
            language=language or self.language,
            confirmed_delta=text_to_emit,
        )

    # ── Async entry-points (for WebSocket / event-loop callers) ──────

    async def process(self) -> Optional[TranscriptionResult]:
        """Process current buffer and return transcription result."""
        if not self.has_enough_audio():
            return None

        audio_chunk = self._get_transcription_audio()

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(_executor, self._transcribe_sync, audio_chunk)
        except Exception as e:
            log.error(f"Live transcription error: {e}", exc_info=True)
            return None

        return self._process_result(result)

    async def flush(self) -> Optional[TranscriptionResult]:
        """Process any remaining audio in the buffer (final call)."""
        with self._lock:
            if len(self.buffer) < int(SAMPLE_RATE * 0.1):
                return TranscriptionResult(
                    confirmed=self._confirmed_text,
                    partial="",
                    language=self.language,
                    confirmed_delta="",
                )

            audio_chunk = self.buffer.copy()
            self.buffer = np.array([], dtype=np.float32)

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(_executor, self._transcribe_sync, audio_chunk)
        except Exception as e:
            log.error(f"Live transcription flush error: {e}", exc_info=True)
            return None

        segments = result.get("segments", [])
        all_words = []
        for seg in segments:
            if hasattr(seg, 'words') and seg.words:
                all_words.extend(seg.words)

        if all_words:
            flush_words = [w.word.strip() for w in all_words]
        else:
            raw = result.get("text", "").strip()
            flush_words = raw.split() if raw else []

        language = result.get("language", self.language)

        flush_text = " ".join(flush_words)
        if flush_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
            flush_words = []

        new_words = flush_words[self._emitted_word_count:]
        text_to_emit = " ".join(new_words)

        if text_to_emit:
            if self._confirmed_text:
                self._confirmed_text += " " + text_to_emit
            else:
                self._confirmed_text = text_to_emit

        return TranscriptionResult(
            confirmed=self._confirmed_text,
            partial="",
            language=language or self.language,
            confirmed_delta=text_to_emit,
        )

    def get_full_transcript(self) -> str:
        """Get the accumulated full transcript so far."""
        return self._confirmed_text

    def reset(self) -> None:
        """Reset all state for a new session."""
        with self._lock:
            self.buffer = np.array([], dtype=np.float32)
        self._prev_text = ""
        self._confirmed_text = ""
        self._emitted_word_count = 0
        self._last_processed_buffer_end = 0.0
        self._stale_cycles = 0
        self._audio_event.clear()
        self._audio_event_sync.clear()
