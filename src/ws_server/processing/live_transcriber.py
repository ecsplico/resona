"""
Live transcription engine with VAD-based chunking and local agreement.

This module provides a stateful transcriber for real-time audio streams.
Each WebSocket session or TUI instance creates its own LiveTranscriber.

Key features:
- Rolling audio buffer with min/max duration control
- Uses faster-whisper's built-in Silero VAD to skip silence
- Local agreement: compares overlapping transcriptions to find stable prefix
- Thread-safe: transcription runs in a thread pool executor
"""
import logging
import asyncio
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import threading
from threading import Lock
from typing import Optional, NamedTuple

from .transcriber_factory import getTranscriber

log = logging.getLogger(__name__)

# Audio config
SAMPLE_RATE = 16000

# Chunking config
MIN_CHUNK_SECONDS = 3       # Minimum audio before attempting transcription
MAX_BUFFER_SECONDS = 30.0   # Cap buffer to prevent memory growth
OVERLAP_SECONDS = 1.0       # Overlap between chunks for context
MIN_NEW_AUDIO_SECONDS = 0.5  # Minimum new audio before signalling readiness


class TranscriptionResult(NamedTuple):
    """Result of a live transcription step."""
    confirmed: str        # Full accumulated confirmed text (stable, won't change)
    partial: str          # Unstable/partial text (may change on next chunk)
    language: str         # Detected language
    confirmed_delta: str = ""  # Only the newly confirmed words in this cycle

# Shared thread pool for transcription (avoids blocking asyncio loop)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="live-asr")


class LiveTranscriber:
    """Stateful live transcriber for a single audio stream/session.

    Usage:
        lt = LiveTranscriber(language="de")
        lt.add_audio(audio_float32_array)
        result = await lt.process()  # returns TranscriptionResult
        final = await lt.flush()     # process remaining buffer
    """

    def __init__(self, language: str = "de", task: str = "transcribe"):
        self.language = language
        self.task = task
        self.buffer = np.array([], dtype=np.float32)
        self._lock = Lock()
        self._transcriber = None  # Lazy-loaded
        self._prev_text = ""      # Previous transcription for local agreement
        self._confirmed_text = "" # Full accumulated confirmed text (for callers)
        self._emitted_word_count = 0  # Words already emitted; skip these on next cycle
        # Event-driven wakeup: set whenever enough new audio arrives
        self._audio_event: asyncio.Event = asyncio.Event()
        self._audio_event_sync: threading.Event = threading.Event()
        # Track how far into the buffer we have already processed
        self._last_processed_buffer_end: float = 0.0  # seconds

    def _get_transcriber(self):
        """Lazy-load the transcriber (expensive model init)."""
        if self._transcriber is None:
            self._transcriber = getTranscriber()
        return self._transcriber

    def add_audio(self, audio: np.ndarray) -> None:
        """Add audio samples to the buffer. Thread-safe."""
        with self._lock:
            self.buffer = np.concatenate([self.buffer, audio])
            # Cap buffer size
            max_samples = int(MAX_BUFFER_SECONDS * SAMPLE_RATE)
            if len(self.buffer) > max_samples:
                self.buffer = self.buffer[-max_samples:]

            # Signal consumers only when meaningful new audio has accumulated
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
        # We need word_timestamps to know exactly where to cut the buffer
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
            word_timestamps=True,  # Crucial for dynamic buffering
        )

    @staticmethod
    def _normalize_word(word: str) -> str:
        """Normalize word for comparison (lower, strip punctuation)."""
        return word.lower().strip(" .,?!:;\"'")

    @staticmethod
    def _find_stable_word_count(prev_words: list[str], curr_words: list[str]) -> int:
        """Find the number of common prefix words between two transcriptions."""
        common_len = 0
        for pw, cw in zip(prev_words, curr_words):
            if LiveTranscriber._normalize_word(pw) == LiveTranscriber._normalize_word(cw):
                common_len += 1
            else:
                break
        return common_len

    def _process_result(self, result: dict) -> Optional[TranscriptionResult]:
        """Shared logic for processing a transcription result dict.

        Used by both the sync and async ``process`` entry-points.
        """
        segments = result.get("segments", [])
        all_words = []
        for seg in segments:
            if hasattr(seg, 'words') and seg.words:
                all_words.extend(seg.words)

        # Reconstruct text from words to ensure alignment
        curr_words_str = [w.word.strip() for w in all_words]

        # Use the raw text if no words (fallback)
        if not all_words:
            curr_text = result.get("text", "").strip()
            curr_words_str = curr_text.split()
        else:
            curr_text = " ".join(curr_words_str)

        # Hallucination filter
        if curr_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
            curr_text = ""
            curr_words_str = []
            all_words = []

        if not curr_text:
            return None

        # Local agreement on WORDS
        prev_words_str = self._prev_text.split()

        confirmed_count = self._find_stable_word_count(prev_words_str, curr_words_str)

        confirmed_words_objs = all_words[:confirmed_count] if all_words else []
        confirmed_words = curr_words_str[:confirmed_count]
        partial_words = curr_words_str[confirmed_count:]

        partial_str = " ".join(partial_words)

        # Deduplicate: skip words already emitted in previous cycles
        text_to_emit = " ".join(confirmed_words[self._emitted_word_count:])
        self._emitted_word_count = len(confirmed_words)

        # Accumulate confirmed text
        if text_to_emit:
            if self._confirmed_text:
                self._confirmed_text += " " + text_to_emit
            else:
                self._confirmed_text = text_to_emit

        # Update Buffer & Context (10s Retention)
        if confirmed_words_objs:
            last_confirmed_word = confirmed_words_objs[-1]
            t_end = last_confirmed_word.end

            # Keep 10 seconds of verified audio context (Safety Overlap)
            t_target = max(0.0, t_end - 10.0)

            # Snap t_target to the nearest word start to avoid cutting words
            t_actual_cut = 0.0
            for w in all_words:
                if w.end > t_target:
                    t_actual_cut = w.start
                    break

            # Slice buffer
            cut_samples = int(t_actual_cut * SAMPLE_RATE)

            with self._lock:
                if cut_samples < len(self.buffer):
                    self.buffer = self.buffer[cut_samples:]
                else:
                    self.buffer = np.array([], dtype=np.float32)

            # Update state for next turn
            retained_confirmed = [w.word.strip() for w in confirmed_words_objs if w.start >= t_actual_cut]
            retained_all = [w.word.strip() for w in all_words if w.start >= t_actual_cut]
            self._prev_text = " ".join(retained_all)
            # After trimming, the next call will re-see retained_confirmed words;
            # set _emitted_word_count to skip them.
            self._emitted_word_count = len(retained_confirmed)

        else:
            # No new confirmation. Buffer unchanged.
            self._prev_text = curr_text

        return TranscriptionResult(
            confirmed=self._confirmed_text,
            partial=partial_str,
            language=result.get("language", self.language),
            confirmed_delta=text_to_emit,
        )

    # ── Synchronous entry-points (for background threads) ────────────

    def process_sync(self) -> Optional[TranscriptionResult]:
        """Process current buffer synchronously.  Call from a background thread."""
        if not self.has_enough_audio():
            return None

        with self._lock:
            self._last_processed_buffer_end = len(self.buffer) / SAMPLE_RATE
            audio_chunk = self.buffer.copy()

        try:
            result = self._transcribe_sync(audio_chunk)
        except Exception as e:
            log.error(f"Live transcription error: {e}", exc_info=True)
            return None

        return self._process_result(result)

    def flush_sync(self) -> Optional[TranscriptionResult]:
        """Process remaining buffer synchronously.  Call from a background thread."""
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

        # Extract words for deduplication
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

        # Hallucination filter
        flush_text = " ".join(flush_words)
        if flush_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
            flush_words = []

        # Deduplicate: skip words already emitted during process() cycles
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

        with self._lock:
            self._last_processed_buffer_end = len(self.buffer) / SAMPLE_RATE
            audio_chunk = self.buffer.copy()

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(_executor, self._transcribe_sync, audio_chunk)
        except Exception as e:
            log.error(f"Live transcription error: {e}", exc_info=True)
            return None

        return self._process_result(result)

    async def flush(self) -> Optional[TranscriptionResult]:
        """Process any remaining audio in the buffer (final call).

        The buffer still contains ~10s of already-confirmed audio (retention
        window), so we must deduplicate against ``_emitted_word_count`` just
        like ``process()`` does – otherwise the retained words get appended a
        second time.
        """
        with self._lock:
            if len(self.buffer) < int(SAMPLE_RATE * 0.1):  # Minimal audio
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

        # Extract words (same logic as process())
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

        # Hallucination filter
        flush_text = " ".join(flush_words)
        if flush_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
            flush_words = []

        # Deduplicate: skip words already emitted during process() cycles
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
        self._audio_event.clear()
        self._audio_event_sync.clear()