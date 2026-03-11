import os
import sys
import time
import threading
import queue
from typing import Callable

import sounddevice as sd
import soundfile as sf
import numpy as np
import asyncio

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Header, Footer, Static, Button
from textual.reactive import reactive
from textual.binding import Binding

import secrets
from core.paths import FILE_PATH

OUTPUT_DIR = FILE_PATH
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 44100))
CHANNELS = int(os.getenv("CHANNELS", 1))
DEVICE = None  # Default system microphone
BLOCK_SIZE = 1024  # Samples per block

# Type alias for audio observer callbacks
AudioObserver = Callable[[np.ndarray], None]


class RecordingSession:
    """Encapsulates all state for a single recording session.

    Replaces the previous module-level globals (stop_event, pause_event,
    audio_queue, etc.) so that each session is self-contained and testable.
    Supports an observer pattern: external code can register callbacks that
    receive a copy of each audio chunk as it arrives from the microphone.
    """

    def __init__(self, filename: str, sample_rate: int = SAMPLE_RATE,
                 channels: int = CHANNELS, device=DEVICE,
                 block_size: int = BLOCK_SIZE):
        self.filename = filename
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.block_size = block_size

        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.save_finished_event = threading.Event()
        self.audio_queue: queue.Queue = queue.Queue()
        self.thread: threading.Thread | None = None

        # Observer list – called with a copy of each audio chunk (np.ndarray)
        self._audio_observers: list[AudioObserver] = []

    def add_audio_observer(self, callback: AudioObserver) -> None:
        """Register *callback* to receive each audio chunk during recording."""
        self._audio_observers.append(callback)

    def remove_audio_observer(self, callback: AudioObserver) -> None:
        """Un-register a previously added observer."""
        try:
            self._audio_observers.remove(callback)
        except ValueError:
            pass

    # ── Audio callback (called from sounddevice thread) ──────────────

    def _audio_callback(self, indata: np.ndarray, frames: int,
                        time_info, status_flags, app_ref: 'MicRecApp'):
        if status_flags:
            app_ref.call_from_thread(
                app_ref.set_status_from_callback, f"Audio Status: {status_flags}",
            )
        if not self.pause_event.is_set() and app_ref.is_recording:
            chunk = indata.copy()
            self.audio_queue.put(chunk)
            # Notify observers
            for obs in self._audio_observers:
                try:
                    obs(chunk)
                except Exception:
                    pass  # Don't let a failing observer break recording

    # ── Recording thread entry-point ─────────────────────────────────

    def run(self, app_ref: 'MicRecApp'):
        """Main recording loop – meant to be run in a daemon thread."""
        app_ref.status_message = "Initializing stream..."
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                device=self.device,
                channels=self.channels,
                blocksize=self.block_size,
                dtype='float32',
                callback=lambda indata, frames, t, status: self._audio_callback(
                    indata, frames, t, status, app_ref,
                ),
            ):
                app_ref.status_message = "\U0001f534 Recording..."
                app_ref.is_recording = True
                app_ref.is_paused = False
                self.pause_event.clear()

                while not self.stop_event.is_set():
                    time.sleep(0.1)

        except Exception as e:
            app_ref.status_message = f"Error: {e}"
        finally:
            app_ref.status_message = "Finishing up..."
            frames_data = []
            while not self.audio_queue.empty():
                try:
                    frames_data.append(self.audio_queue.get_nowait())
                except queue.Empty:
                    break

            if frames_data:
                audio_data = np.concatenate(frames_data, axis=0)
                try:
                    os.makedirs(os.path.dirname(self.filename), exist_ok=True)
                    sf.write(self.filename, audio_data, self.sample_rate)
                    app_ref.status_message = f"\u2705 Saved: {os.path.basename(self.filename)}"
                    self.save_finished_event.set()
                except Exception as e:
                    app_ref.status_message = f"Error saving: {e}"
                    self.save_finished_event.set()
            else:
                app_ref.status_message = "No audio data to save."
                self.save_finished_event.set()

            app_ref.can_exit_now = True

    def start(self, app_ref: 'MicRecApp'):
        """Start the recording in a daemon thread."""
        self.thread = threading.Thread(target=self.run, args=(app_ref,), daemon=True)
        self.thread.start()

    def stop(self):
        """Signal the recording thread to stop."""
        self.stop_event.set()

    def join(self, timeout: float = 2.0):
        """Wait for the recording thread to finish."""
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)

    def drain_queue(self):
        """Discard remaining audio in the queue."""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def reset_events(self):
        """Clear all events for potential reuse (prefer creating a new session)."""
        self.stop_event.clear()
        self.pause_event.clear()
        self.save_finished_event.clear()


class MicRecApp(App):
    TITLE = "\U0001f3a4 MicRec - CLI Audio Recorder"
    CSS_PATH = "recorder.tcss"
    INLINE_PADDING = 0

    BINDINGS = [
        Binding("q", "quit_recording", "Quit App", show=True, priority=True),
        Binding("space", "toggle_record_pause", "Record/Pause", show=True, priority=True),
        Binding("d", "discard_recording", "Discard", show=True),
        Binding("ctrl+c", "request_quit_app", "Force Quit", show=False),
    ]

    # Reactive variables for UI updates
    status_message = reactive("Press 'Record' to begin.")
    elapsed_time_str = reactive("00:00:00")
    is_recording = reactive(False)
    is_paused = reactive(False)
    record_button_label = reactive("Record")
    record_button_variant = reactive("primary")

    def __init__(self):
        super().__init__()
        self._session: RecordingSession | None = None
        self.start_time = 0.0
        self.output_filename = ""
        self._timer_update_elapsed = None
        self.can_exit_now = False
        self._current_paused_duration = 0.0
        self._last_pause_time = 0.0
        self._exit_checker_timer = None
        self._is_saving_and_restarting = False

    @property
    def session(self) -> RecordingSession | None:
        """The current recording session, if any."""
        return self._session

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="header_info_container"):
            yield Static(self.status_message, id="status_display", classes="header_info_element")
            yield Static(self.elapsed_time_str, id="elapsed_display", classes="header_info_element")
        with Container(id="main_container"):
            with Horizontal(id="controls_container"):
                yield Button(self.record_button_label, id="record_pause_button", variant=self.record_button_variant, classes="control_element")
                yield Button("Save", id="save_button", variant="success", disabled=True, classes="control_element")
                yield Button("Discard", id="discard_button", variant="error", disabled=True, classes="control_element")
        yield Footer()

    def on_mount(self) -> None:
        if not os.path.exists(OUTPUT_DIR):
            try:
                os.makedirs(OUTPUT_DIR)
            except OSError as e:
                self.status_message = f"Error creating output dir {OUTPUT_DIR}: {e}"
                self.query_one("#record_pause_button", Button).disabled = True
                self.query_one("#save_button", Button).disabled = True
                return

        self._update_record_pause_button_ui()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "record_pause_button":
            await self.action_toggle_record_pause()
        elif button_id == "save_button":
            await self.action_save_and_new_recording()
        elif button_id == "discard_button":
            self.action_discard_recording()

    def _update_record_pause_button_ui(self):
        save_button = self.query_one("#save_button", Button)
        discard_button = self.query_one("#discard_button", Button)

        if not self.is_recording:
            self.record_button_label = "\U0001f534 Record"
            self.record_button_variant = "primary"
            save_button.disabled = True
            discard_button.disabled = True
        elif self.is_paused:
            self.record_button_label = "\u25b6\ufe0f Resume"
            self.record_button_variant = "success"
            save_button.disabled = False
            discard_button.disabled = False
        else:
            self.record_button_label = "\u23f8\ufe0f Pause"
            self.record_button_variant = "warning"
            save_button.disabled = False
            discard_button.disabled = False

        save_button.label = "\U0001f4be Save"
        save_button.variant = "primary"
        discard_button.label = "\U0001f5d1\ufe0f Discard"
        discard_button.variant = "error"

    async def action_save_and_new_recording(self) -> None:
        if not self.is_recording or self._is_saving_and_restarting:
            return

        self._is_saving_and_restarting = True
        self.status_message = "Saving current recording..."
        self.query_one("#save_button", Button).disabled = True
        self.query_one("#record_pause_button", Button).disabled = True

        if self._session:
            self._session.save_finished_event.clear()
            self._session.stop()
            await self._wait_for_save_completion()
            self._session.join(timeout=2.0)

        self.is_recording = False
        self.is_paused = False
        self.start_time = 0.0
        self._current_paused_duration = 0.0
        self.elapsed_time_str = "00:00:00"
        self.can_exit_now = True

        self._session = None

        self._update_record_pause_button_ui()
        self.query_one("#record_pause_button", Button).disabled = False
        self._is_saving_and_restarting = False

    async def _wait_for_save_completion(self):
        if self._session is None:
            return
        while not self._session.save_finished_event.is_set():
            await asyncio.sleep(0.1)
        self.log("Save finished event received.")

    def start_recording_action(self):
        if self.is_recording and not self._is_saving_and_restarting:
            return

        self.log("Starting recording action...")

        name_new = f"{secrets.token_hex(10)}.wav"
        self.output_filename = os.path.join(OUTPUT_DIR, name_new)

        # Create a fresh session
        self._session = RecordingSession(filename=self.output_filename)

        self.is_recording = True
        self.is_paused = False
        self.start_time = time.monotonic()
        self._current_paused_duration = 0.0
        self.can_exit_now = False
        self.update_elapsed_time()
        self.status_message = "\U0001f534 Recording..."
        self._update_record_pause_button_ui()

        self._session.start(self)
        self._update_record_pause_button_ui()

    def update_elapsed_time(self) -> None:
        if self.is_recording and not self.is_paused:
            current_elapsed = time.monotonic() - self.start_time - self._current_paused_duration
            duration = int(current_elapsed)
            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.elapsed_time_str = f"{hours:02}:{minutes:02}:{seconds:02}"

        if self.is_recording:
            if self._timer_update_elapsed:
                self._timer_update_elapsed.reset()
            else:
                self._timer_update_elapsed = self.set_interval(1.0, self.update_elapsed_time)
        elif self._timer_update_elapsed:
            self._timer_update_elapsed.stop()
            self._timer_update_elapsed = None

    def action_quit_recording(self) -> None:
        if self._is_saving_and_restarting:
            self.status_message = "Saving in progress, please wait to quit."
            return

        if self.is_recording and self._session:
            self.status_message = "Stopping and saving before exiting..."
            self._session.save_finished_event.clear()
            self._session.stop()

            if self._timer_update_elapsed:
                self._timer_update_elapsed.stop()
                self._timer_update_elapsed = None

            if self._exit_checker_timer:
                try:
                    self._exit_checker_timer.stop()
                except Exception:
                    pass
                self._exit_checker_timer = None
            self._exit_checker_timer = self.set_interval(0.2, self._check_exit_conditions_after_save)
        else:
            self.exit()

    async def _check_exit_conditions_after_save(self):
        session_done = self._session is None or self._session.save_finished_event.is_set()
        if session_done or self.can_exit_now:
            if self._exit_checker_timer:
                try:
                    self._exit_checker_timer.stop()
                except Exception:
                    pass
                self._exit_checker_timer = None
            self.log("Save finished, exiting application.")
            self.exit()

    def action_request_quit_app(self) -> None:
        session_stop_set = self._session and self._session.stop_event.is_set()
        if self.is_recording and not session_stop_set:
            self.status_message = "Ctrl+C pressed. Stopping and saving..."
            self.action_quit_recording()
        elif not self.is_recording and self.can_exit_now:
            self.exit()
        elif not self.is_recording and not self.can_exit_now and session_stop_set:
            self.status_message = "Saving in progress. Please wait."
        else:
            self.exit()

    def action_discard_recording(self) -> None:
        if not self.is_recording:
            return

        self.log("Discarding recording...")

        if self._session:
            self._session.stop()
            self._session.drain_queue()
            self._session.join(timeout=1.0)

        self.is_recording = False
        self.is_paused = False
        self.start_time = 0.0
        self._current_paused_duration = 0.0
        self.elapsed_time_str = "00:00:00"
        self.status_message = "Recording discarded."
        self.can_exit_now = True

        if self._timer_update_elapsed:
            self._timer_update_elapsed.stop()
            self._timer_update_elapsed = None

        self._session = None
        self._update_record_pause_button_ui()

    def action_pause_resume_recording(self) -> None:
        if not self.is_recording or not self._session:
            return

        if self.is_paused:
            self._session.pause_event.clear()
            self.is_paused = False
            self._current_paused_duration += (time.monotonic() - self._last_pause_time)
            self.status_message = "\U0001f534 Recording..."
            self.update_elapsed_time()
        else:
            self._session.pause_event.set()
            self.is_paused = True
            self._last_pause_time = time.monotonic()
            self.status_message = "\u23f8\ufe0f Paused"
        self._update_record_pause_button_ui()

    # Watch methods for reactive variables
    def watch_status_message(self, new_message: str) -> None:
        try:
            self.query_one("#status_display", Static).update(new_message)
        except Exception:
            pass

    def watch_elapsed_time_str(self, new_time_str: str) -> None:
        try:
            self.query_one("#elapsed_display", Static).update(new_time_str)
        except Exception:
            pass

    def watch_record_button_label(self, new_label: str) -> None:
        try:
            self.query_one("#record_pause_button", Button).label = new_label
        except Exception:
            pass

    def watch_record_button_variant(self, new_variant: str) -> None:
        try:
            self.query_one("#record_pause_button", Button).variant = new_variant
        except Exception:
            pass

    def set_status_from_callback(self, message: str):
        self.status_message = message

    # ── Shared utilities for subclasses (ws_live, ws_ui) ─────────────

    def log_msg(self, msg: str) -> None:
        """Write a timestamped message to the #log_display RichLog widget."""
        try:
            from textual.widgets import RichLog
            log_display = self.query_one("#log_display", RichLog)
            timestamp = time.strftime("%H:%M:%S")
            log_display.write(f"[{timestamp}] {msg}")
        except Exception:
            pass

    def action_clear_logs(self) -> None:
        """Clear the log display."""
        try:
            from textual.widgets import RichLog
            log_display = self.query_one("#log_display", RichLog)
            log_display.clear()
            self.notify("Logs cleared.", title="Cleared")
        except Exception as e:
            self.notify(f"Error: {e}", title="Error", severity="error")

    def manual_copy_fallback(self, text: str) -> bool:
        """Try to copy *text* to clipboard using system tools."""
        import subprocess
        import shutil

        commands = [
            ("wl-copy", []),
            ("xclip", ["-selection", "clipboard"]),
            ("xsel", ["-b", "-i"]),
        ]
        for cmd, args in commands:
            if shutil.which(cmd):
                try:
                    proc = subprocess.Popen(
                        [cmd] + args, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                    )
                    proc.communicate(input=text.encode("utf-8"))
                    if proc.returncode == 0:
                        return True
                except Exception:
                    continue
        return False

    # Debounce for record/pause toggle (shared across subclasses)
    _last_toggle_time: float = 0.0

    async def action_toggle_record_pause(self) -> None:
        now = time.monotonic()
        if now - self._last_toggle_time < 0.5:
            return
        self._last_toggle_time = now

        if not self.is_recording:
            self.start_recording_action()
        else:
            self.action_pause_resume_recording()
        self._update_record_pause_button_ui()


def run_mic_rec_app():
    """Initializes and runs the MicRecApp application."""
    import logging as _logging
    _logging.root.handlers.clear()
    _logging.root.addHandler(_logging.NullHandler())

    if not os.path.exists(OUTPUT_DIR):
        try:
            os.makedirs(OUTPUT_DIR)
        except Exception as e:
            sys.stderr.write(f"Error: Could not create output directory {OUTPUT_DIR}: {e}\n")
            sys.exit(1)

    try:
        sd.check_input_settings(device=DEVICE, samplerate=SAMPLE_RATE, channels=CHANNELS)
    except Exception as e:
        sys.stderr.write(f"Error initializing audio input: {e}\n")
        sys.exit(1)

    app = MicRecApp()
    app.run(inline=True)


if __name__ == "__main__":
    run_mic_rec_app()
