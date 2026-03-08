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
    confirmed: str   # Stable/confirmed text (won't change)
    partial: str     # Unstable/partial text (may change on next chunk)
    language: str    # Detected language


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
        self._confirmed_text = "" # Accumulated confirmed text
        self._overlap_text = ""   # Text corresponding to retained 10s audio
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

    async def process(self) -> Optional[TranscriptionResult]:
        """Process current buffer and return transcription result."""
        if not self.has_enough_audio():
            return None

        # Mark how far we have processed so add_audio() won't fire prematurely
        with self._lock:
            self._last_processed_buffer_end = len(self.buffer) / SAMPLE_RATE

        with self._lock:
            # Transcribe the ENTIRE buffer (we'll slice it later based on results)
            # buffer is already capped by add_audio
            audio_chunk = self.buffer.copy()

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(_executor, self._transcribe_sync, audio_chunk)
        except Exception as e:
            log.error(f"Live transcription error: {e}", exc_info=True)
            return None

        # Flatten segments to words
        # result is a tuple (segments_generator, info)
        # But _transcribe_sync consumes the generator and returns a dict (in transcriber_fast_whisper.py)
        # Wait, transcriber_fast_whisper.py currently returns:
        # { "language": ..., "segments": [segment_objs], "text": ... }
        # faster_whisper segments have .words list if word_timestamps=True
        
        segments = result.get("segments", [])
        all_words = []
        for seg in segments:
            if hasattr(seg, 'words') and seg.words:
                all_words.extend(seg.words)
            else:
                # Fallback if no word timestamps (shouldn't happen with word_timestamps=True)
                # Ensure we handle this gracefully
                pass

        # Reconstruct text from words to ensure alignment
        curr_text_from_words = " ".join([w.word.strip() for w in all_words])
        curr_words_str = [w.word.strip() for w in all_words]

        # Use the raw text if no words (fallback)
        if not all_words:
            curr_text = result.get("text", "").strip()
            curr_words_str = curr_text.split()
        else:
            curr_text = curr_text_from_words

        # Hallucination filter
        if curr_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
             curr_text = ""
             curr_words_str = []
             all_words = []

        if not curr_text:
            return None

        # Local agreement on WORDS
        # prev_text needs to be stored as words too ideally, but string splitting is usually fine
        prev_words_str = self._prev_text.split()
        
        confirmed_count = self._find_stable_word_count(prev_words_str, curr_words_str)
        
        confirmed_words_objs = all_words[:confirmed_count] if all_words else []
        confirmed_words = curr_words_str[:confirmed_count]
        partial_words = curr_words_str[confirmed_count:]
        
        full_confirmed_str = " ".join(confirmed_words)
        partial_str = " ".join(partial_words)
        
        # 1. Deduplicate Emission
        text_to_emit = full_confirmed_str
        
        if self._overlap_text:
            overlap_words = self._overlap_text.split()
            skip_count = len(overlap_words)
            
            # Check for simple prefix match (strict) or normalized match
            # We prefer normalized match to handle punctuation changes
            match_len = self._find_stable_word_count(overlap_words, confirmed_words)
            
            # If match_len is close to overlap length (e.g. all words match), use match_len
            # In the duplication case, match_len was likely 0 because of punctuation.
            # Now with normalization, it should be len(overlap_words).
            
            if match_len > 0:
                 # Trust the new version of text (which might have updated punctuation)
                 # Skip the matched part
                 text_to_emit = " ".join(confirmed_words[match_len:])
            else:
                 # No normalized match found?
                 # If we have overlap text but ZERO match, it means the text changed completely.
                 # This is rare if buffer is same.
                 # Should we emit everything? Yes.
                 text_to_emit = full_confirmed_str

        # Accumulate confirmed text
        if text_to_emit:
            if self._confirmed_text:
                self._confirmed_text += " " + text_to_emit
            else:
                self._confirmed_text = text_to_emit

        # 2. Update Buffer & Context (10s Retention)
        if confirmed_words_objs:
            last_confirmed_word = confirmed_words_objs[-1]
            t_end = last_confirmed_word.end
            
            # Keep 10 seconds of verified audio context (Safety Overlap)
            # This ensures we don't cut words at boundaries and provides context
            t_target = max(0.0, t_end - 10.0)
            
            # Snap t_target to the nearest word start to avoid cutting words
            t_actual_cut = 0.0
            for w in all_words:
                if w.end > t_target:
                    # Found the first word that effectively crosses or starts the retention zone
                    t_actual_cut = w.start
                    break
            
            # Slice buffer
            cut_samples = int(t_actual_cut * SAMPLE_RATE)
            
            with self._lock:
                if cut_samples < len(self.buffer):
                    self.buffer = self.buffer[cut_samples:]
                else:
                    self.buffer = np.array([], dtype=np.float32)

            # 3. Update State for Next Turn
            # _overlap_text: confirmed text corresponding to KEPT audio
            retained_confirmed = [w.word.strip() for w in confirmed_words_objs if w.start >= t_actual_cut]
            self._overlap_text = " ".join(retained_confirmed)
            
            # _prev_text: All text (confirmed + partial) corresponding to KEPT audio
            retained_all = [w.word.strip() for w in all_words if w.start >= t_actual_cut]
            self._prev_text = " ".join(retained_all)
            
        else:
            # No new confirmation. Buffer unchanged (except potential max cap).
            # _prev_text is the full current text for next comparison
            self._prev_text = curr_text
            # _overlap_text remains valid (matches start of buffer)
            pass

        return TranscriptionResult(
            confirmed=self._confirmed_text,
            partial=partial_str,
            language=result.get("language", self.language),
        )

    async def flush(self) -> Optional[TranscriptionResult]:
        """Process any remaining audio in the buffer (final call)."""
        # Logic remains mostly similar but we treat everything as final
        with self._lock:
            if len(self.buffer) < 16000 * 0.1: # Minimal audio
                 return TranscriptionResult(
                        confirmed=self._confirmed_text,
                        partial="",
                        language=self.language,
                    )

            audio_chunk = self.buffer.copy()
            self.buffer = np.array([], dtype=np.float32)

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(_executor, self._transcribe_sync, audio_chunk)
        except Exception as e:
            log.error(f"Live transcription flush error: {e}", exc_info=True)
            return None

        final_text = result.get("text", "").strip()
        language = result.get("language", self.language)

        if final_text.lower().strip('.!') in ["vielen dank", "untertitel", "thank you"]:
             final_text = ""
        
        # Since _confirmed_text contains everything UP TO this buffer,
        # and this buffer produced final_text, we just append it.
        if final_text:
             if self._confirmed_text:
                 self._confirmed_text += " " + final_text
             else:
                 self._confirmed_text = final_text

        return TranscriptionResult(
            confirmed=self._confirmed_text,
            partial="",
            language=language or self.language,
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
        self._overlap_text = ""
        self._last_processed_buffer_end = 0.0
        self._audio_event.clear()
        self._audio_event_sync.clear()