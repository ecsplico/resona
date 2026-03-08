"""
WS-Live TUI: Live transcription with real-time display.

Records audio from the microphone and transcribes it in real-time using
the LiveTranscriber engine. Displays partial (dimmed) and confirmed (bold)
text in a "Live" tab.

Extends MicRecApp for consistent recording controls.
"""
import os
import threading
import time
import queue
import numpy as np

from textual.app import ComposeResult
from textual.widgets import TabbedContent, TabPane, RichLog, Header, Footer, Static, Button
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message

from recorder.micrec import MicRecApp
from ws_server.processing.live_transcriber import LiveTranscriber, SAMPLE_RATE as ASR_SAMPLE_RATE

import sounddevice as sd

# Audio capture settings (must match recorder defaults)
MIC_SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 44100))
MIC_CHANNELS = int(os.getenv("CHANNELS", 1))
MIC_BLOCK_SIZE = 1024


class LiveTranscriptionMessage(Message):
    """Message posted from the transcription thread to the UI."""
    def __init__(self, confirmed: str, partial: str, is_final: bool = False):
        super().__init__()
        self.confirmed = confirmed
        self.partial = partial
        self.is_final = is_final


class WSLiveApp(MicRecApp):
    CSS_PATH = "css/style.tcss"
    TITLE = "🎙️ WS-Live - Live Transcription"

    def __init__(self):
        super().__init__()
        self._live_transcriber: LiveTranscriber | None = None
        self._live_thread: threading.Thread | None = None
        self._live_stop_event = threading.Event()
        self._audio_queue: queue.Queue = queue.Queue()
        self._full_transcript = ""
        self._last_confirmed_len = 0
        self._displayed_confirmed = ""  # Accumulated confirmed text shown
        self._last_confirmed_text = ""  # Store previous confirmed to detect resets
        self.results_map = {}  # For copy support

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
                yield Button("📋 Copy", id="copy_button", variant="primary", disabled=True, classes="control_element")
                yield Button("🗑️ Clear", id="clear_results_button", variant="warning", classes="control_element")
                yield Button("🗑️ Logs", id="clear_logs_button", variant="warning", classes="control_element")

        with TabbedContent(id="results_tabs"):
            with TabPane("Live", id="tab_live"):
                with VerticalScroll(id="live_scroll"):
                    yield Static("", id="live_display", markup=True)
            with TabPane("Logs", id="tab_logs"):
                yield RichLog(id="log_display", wrap=True)

        yield Footer()

    async def on_mount(self) -> None:
        # Call grandparent's on_mount (MicRecApp), skip the recording-specific init
        # We only need the output dir check from MicRecApp
        super().on_mount()

        # Connect Python logging to the TUI Logs tab
        import logging
        class TUILogHandler(logging.Handler):
            def __init__(self, app_instance):
                super().__init__()
                self.app_instance = app_instance
            
            def emit(self, record):
                msg = self.format(record)
                self.app_instance.call_from_thread(self.app_instance.log_msg, f"[dim]{record.name}:[/dim] {msg}")

        # Configure root logger to output to TUI
        root_logger = logging.getLogger()
        handler = TUILogHandler(self)
        handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)

        # Monkey-patch the audio callback to intercept audio for live transcription
        # This prevents the race condition of peeking at the save queue
        import recorder.micrec as micrec_module
        
        # Store original if not already patched (basic check)
        if not hasattr(micrec_module, "_original_audio_callback"):
            micrec_module._original_audio_callback = micrec_module.audio_callback


        # Track last confirmed length to show only deltas
            def intercepted_callback(indata, frames, time, status, app_ref):
                # Always call original for saving/status logic
                micrec_module._original_audio_callback(indata, frames, time, status, app_ref)
                
                # If this is our app (WSLiveApp), feed our local queue
                if isinstance(app_ref, WSLiveApp):
                    if app_ref.is_recording and not app_ref.is_paused:
                        # Put a COPY into our local queue
                        app_ref._audio_queue.put(indata.copy())

            # Apply patch
            micrec_module.audio_callback = intercepted_callback
            self.log_msg("Audio callback intercepted for live transcription.")

        self.log_msg("WS-Live ready. Press Record to start live transcription.")

    def log_msg(self, msg: str) -> None:
        """Write to the log tab."""
        try:
            log_display = self.query_one("#log_display", RichLog)
            timestamp = time.strftime("%H:%M:%S")
            log_display.write(f"[{timestamp}] {msg}")
        except Exception:
            pass

    # ── Recording overrides ──────────────────────────────────────────

    def start_recording_action(self):
        """Override: start recording AND live transcription."""
        # Start the standard recording (saves WAV file)
        super().start_recording_action()

        # Start live transcription
        self._start_live_transcription()

    async def action_save_and_new_recording(self) -> None:
        """Override: stop live transcription, then save."""
        self._stop_live_transcription()
        await super().action_save_and_new_recording()

        # Store the transcript for the copy button
        if self._full_transcript:
            self.results_map["tab_live"] = self._full_transcript
            self.log_msg(f"Transcript saved ({len(self._full_transcript)} chars)")

    def action_discard_recording(self) -> None:
        """Override: stop live transcription, then discard."""
        self._stop_live_transcription()
        super().action_discard_recording()

    # ── Live Transcription Logic ─────────────────────────────────────

    def _start_live_transcription(self):
        """Initialize and start the live transcription pipeline."""
        self.log_msg("Starting live transcription...")

        # Clear previous state
        self._full_transcript = ""
        self._live_stop_event.clear()
        
        # Drain the local queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        # Clear the live display
        try:
            live_display = self.query_one("#live_display", Static)
            live_display.update("")
            self._last_confirmed_len = 0
            self._displayed_confirmed = ""
            self._last_confirmed_text = ""
        except Exception:
            pass

        # Create a fresh transcriber
        self._live_transcriber = LiveTranscriber(language="de")

        # Start the audio capture -> transcription thread
        self._live_thread = threading.Thread(
            target=self._live_transcription_worker,
            daemon=True,
        )
        self._live_thread.start()

        # Start a timer to feed audio from the recorder's queue to our transcriber
        self.set_interval(0.05, self._feed_audio_to_transcriber, name="audio_feed_timer")

        self.log_msg("Live transcription started")

    def _stop_live_transcription(self):
        """Stop the live transcription pipeline."""
        self._live_stop_event.set()

        # Stop the audio feed timer
        try:
            for timer in self.query("Timer"):
                pass  # Textual doesn't expose named timer removal easily
        except Exception:
            pass

        if self._live_thread and self._live_thread.is_alive():
            self._live_thread.join(timeout=3.0)

        # Get final transcript
        if self._live_transcriber:
            self._full_transcript = self._live_transcriber.get_full_transcript()

        self.log_msg("Live transcription stopped")

    def _feed_audio_to_transcriber(self):
        """Timer callback: read audio data from LOCAL queue and feed to transcriber."""
        if self._live_transcriber is None:
            return

        chunks_processed = 0
        # Process up to 50 chunks from our dedicated queue
        while not self._audio_queue.empty() and chunks_processed < 50:
            try:
                chunk = self._audio_queue.get_nowait()
                
                # Note: We rely on the callback to only sending us data when not paused
                # But we can check is_paused here too just in case
                if not self.is_paused:
                    # Handle stereo -> mono if needed
                    if chunk.shape[1] > 1:
                        chunk = chunk.mean(axis=1, keepdims=True)

                    # Chunk is already float32 [-1, 1] from sounddevice (dtype='float32')
                    audio_float = chunk.flatten().astype(np.float32)

                    # Resample using torchaudio for high quality
                    if MIC_SAMPLE_RATE != ASR_SAMPLE_RATE:
                        import torch
                        import torchaudio.functional as F
                        
                        # Convert to tensor
                        audio_tensor = torch.from_numpy(audio_float)
                        # Resample
                        resampled_tensor = F.resample(audio_tensor, MIC_SAMPLE_RATE, ASR_SAMPLE_RATE)
                        audio_resampled = resampled_tensor.numpy()
                    else:
                        audio_resampled = audio_float

                    self._live_transcriber.add_audio(audio_resampled)
                
                chunks_processed += 1
            except queue.Empty:
                break

    def _live_transcription_worker(self):
        """Background thread: periodically process audio and post results to UI."""
        import asyncio

        while not self._live_stop_event.is_set():
            # Wait for new audio or stop signal (up to 1s to stay responsive)
            if self._live_transcriber is not None:
                signalled = self._live_transcriber._audio_event_sync.wait(timeout=1.0)
                if signalled:
                    self._live_transcriber._audio_event_sync.clear()
            else:
                self._live_stop_event.wait(timeout=0.1)
                continue

            if self._live_transcriber is None:
                continue

            if not self._live_transcriber.has_enough_audio():
                continue

            try:
                # Run the async process() in a new event loop (we're in a thread)
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(self._live_transcriber.process())
                finally:
                    loop.close()

                if result is None:
                    continue

                # Post the result to the UI thread
                self.call_from_thread(
                    self._update_live_display,
                    result.confirmed,
                    result.partial,
                    False,
                )

            except Exception as e:
                self.call_from_thread(self.log_msg, f"Transcription error: {e}")

        # Final flush
        if self._live_transcriber:
            try:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(self._live_transcriber.flush())
                finally:
                    loop.close()

                if result and result.confirmed:
                    self.call_from_thread(
                        self._update_live_display,
                        result.confirmed,
                        "",
                        True,
                    )
            except Exception as e:
                self.call_from_thread(self.log_msg, f"Flush error: {e}")

    def _update_live_display(self, confirmed: str, partial: str, is_final: bool):
        """Update the live transcription display (called from UI thread)."""
        try:
            live_display = self.query_one("#live_display", Static)

            # Extract only the newly confirmed portion (delta)
            confirmed_delta = ""
            if confirmed:
                # Check if we're seeing a completely new text (reset)
                if confirmed == self._last_confirmed_text:
                    # Same text, no new content
                    confirmed_delta = ""
                elif confirmed.startswith(self._last_confirmed_text):
                    # New text extends previous confirmed
                    confirmed_delta = confirmed[len(self._last_confirmed_text):]
                else:
                    # Different prefix (reset scenario)
                    confirmed_delta = confirmed
                    self._last_confirmed_len = 0

                # Accumulate delta into displayed confirmed text
                if confirmed_delta:
                    self._displayed_confirmed += confirmed_delta

            # Build display parts
            parts = []
            if self._displayed_confirmed:
                parts.append(self._displayed_confirmed)
            if partial:
                parts.append(f"[dim]{partial}[/dim]")
            live_display.update("\n".join(parts))

            # Track for next cycle
            if confirmed:
                self._last_confirmed_len = len(confirmed)
                self._last_confirmed_text = confirmed
            if is_final:
                self._full_transcript = confirmed
                self.log_msg("Transcription finalized")

            # Enable copy button when there's content
            try:
                copy_btn = self.query_one("#copy_button", Button)
                copy_btn.disabled = not bool(confirmed)
            except Exception:
                pass

        except Exception as e:
            self.log_msg(f"Display update error: {e}")

    # ── Button Handlers ──────────────────────────────────────────────

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "record_pause_button":
            await self.action_toggle_record_pause()
        elif button_id == "save_button":
            await self.action_save_and_new_recording()
        elif button_id == "discard_button":
            self.action_discard_recording()
        elif button_id == "copy_button":
            self.action_copy_transcription()
        elif button_id == "clear_results_button":
            self.action_clear_live()
        elif button_id == "clear_logs_button":
            self.action_clear_logs()

    def action_clear_live(self):
        """Clear the live transcription display."""
        try:
            live_display = self.query_one("#live_display", Static)
            live_display.update("")
            self._last_confirmed_len = 0
            self._displayed_confirmed = ""
            self._last_confirmed_text = ""
            self._full_transcript = ""
            self.results_map.pop("tab_live", None)
            self.query_one("#copy_button", Button).disabled = True
            self.notify("Live display cleared.", title="Cleared")
        except Exception as e:
            self.notify(f"Error: {e}", title="Error", severity="error")

    def action_clear_logs(self):
        """Clear the log display."""
        try:
            log_display = self.query_one("#log_display", RichLog)
            log_display.clear()
            self.notify("Logs cleared.", title="Cleared")
        except Exception as e:
            self.notify(f"Error: {e}", title="Error", severity="error")

    def manual_copy_fallback(self, text: str) -> bool:
        """Try clipboard with system tools."""
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
                    process = subprocess.Popen([cmd] + args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
                    process.communicate(input=text.encode('utf-8'))
                    if process.returncode == 0:
                        return True
                except Exception:
                    continue
        return False

    def action_copy_transcription(self):
        """Copy the current transcript to clipboard."""
        text = self._full_transcript or self.results_map.get("tab_live", "")
        if not text and self._live_transcriber:
            text = self._live_transcriber.get_full_transcript()

        if text:
            success = False
            try:
                self.app.copy_to_clipboard(text)
                success = True
            except Exception:
                pass
            if not success:
                success = self.manual_copy_fallback(text)
            if success:
                self.notify("Copied to clipboard!", title="Success", timeout=2.0)
            else:
                self.notify("Failed to copy", title="Error", severity="error")
        else:
            self.notify("No transcription to copy.", title="Info")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle tab switching for copy button state."""
        try:
            active_id = event.pane.id
            copy_btn = self.query_one("#copy_button", Button)
            if active_id == "tab_live" and self._full_transcript:
                copy_btn.disabled = False
            elif active_id == "tab_logs":
                copy_btn.disabled = True
            else:
                copy_btn.disabled = True
        except Exception:
            pass

    # Debounce control
    _last_toggle_time = 0.0

    async def action_toggle_record_pause(self) -> None:
        """Override with debounce."""
        now = time.monotonic()
        if now - self._last_toggle_time < 0.5:
            return
        self._last_toggle_time = now
        self.log_msg(f"Toggle Record/Pause. Recording={self.is_recording}, Paused={self.is_paused}")
        await super().action_toggle_record_pause()

    def exit(self, *args, **kwargs):
        self._live_stop_event.set()
        if self._live_thread and self._live_thread.is_alive():
            self._live_thread.join(timeout=2.0)
        super().exit(*args, **kwargs)
