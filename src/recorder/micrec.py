import os
import sys
import time
import datetime
import threading
import queue # For passing audio data from recording thread to main/saving logic

import sounddevice as sd
import soundfile as sf
import numpy as np

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Header, Footer, Static, Label, Button
from textual.reactive import reactive
from textual.binding import Binding # Added for key bindings

from core.paths import INBOX_PATH

OUTPUT_DIR = INBOX_PATH # Use INBOX_PATH from core.paths
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 44100))
CHANNELS = int(os.getenv("CHANNELS", 1))
DEVICE = None # Default system microphone
BLOCK_SIZE = 1024 # Samples per block

# Threading control
stop_event = threading.Event()
pause_event = threading.Event() # When set, recording is paused
audio_queue = queue.Queue()
recording_thread = None
save_finished_event = threading.Event() # New event to signal save completion

# Global app instance reference for the recording thread
_app_instance_ref = None

def record_audio_thread(filename: str, app_ref: 'MicRecApp'):
    """
    Records audio and puts frames into audio_queue.
    Saves to filename when stop_event is set.
    """
    global audio_queue, stop_event, pause_event

    app_ref.status_message = "Initializing stream..."
    try:
        with sd.InputStream(samplerate=SAMPLE_RATE,
                             device=DEVICE,
                             channels=CHANNELS,
                             blocksize=BLOCK_SIZE,
                             dtype='float32', # SoundFile prefers float32 or int16
                             callback=lambda indata, frames, time, status: audio_callback(indata, frames, time, status, app_ref)):
            app_ref.status_message = "🔴 Recording..."
            app_ref.is_recording = True
            app_ref.is_paused = False
            pause_event.clear() # Ensure not paused at start

            while not stop_event.is_set():
                if pause_event.is_set():
                    time.sleep(0.1)
                    continue
                time.sleep(0.1) # Keep the thread alive and responsive

    except Exception as e:
        app_ref.status_message = f"Error: {e}"
    finally:
        app_ref.status_message = "Finishing up..."
        frames_data = []
        while not audio_queue.empty():
            try:
                frames_data.append(audio_queue.get_nowait())
            except queue.Empty:
                break # Should not happen if audio_queue.empty() is checked

        if frames_data:
            audio_data = np.concatenate(frames_data, axis=0)
            try:
                os.makedirs(os.path.dirname(filename), exist_ok=True)
                sf.write(filename, audio_data, SAMPLE_RATE)
                app_ref.status_message = f"✅ Saved: {os.path.basename(filename)}"
                save_finished_event.set() # Signal that saving is done
            except Exception as e:
                app_ref.status_message = f"Error saving: {e}"
                save_finished_event.set() # Also signal if error during save to unblock
        else:
            app_ref.status_message = "No audio data to save."
            save_finished_event.set() # Signal if no data to unblock

        # These states will be reset by start_recording_action or quit
        # app_ref.is_recording = False
        # app_ref.is_paused = False
        app_ref.can_exit_now = True # Signal that the app can exit if quitting


def audio_callback(indata: np.ndarray, frames: int, time_info, status_flags, app_ref: 'MicRecApp'):
    """
    This is called by sounddevice for each new block of audio data.
    """
    if status_flags:
        # Use app_ref.call_from_thread for UI updates from thread
        app_ref.call_from_thread(app_ref.set_status_from_callback, f"Audio Status: {status_flags}")
    if not pause_event.is_set() and app_ref.is_recording:
        audio_queue.put(indata.copy())


class MicRecApp(App):
    TITLE = "🎤 MicRec - CLI Audio Recorder"
    CSS_PATH = "recorder.tcss"
    INLINE_PADDING = 0 # For compact inline mode

    BINDINGS = [
        Binding("q", "quit_recording", "Quit App", show=True, priority=True),
        Binding("space", "toggle_record_pause", "Record/Pause", show=True, priority=True),
        Binding("d", "discard_recording", "Discard", show=True), # Added discard binding
        Binding("ctrl+c", "request_quit_app", "Force Quit", show=False) # Handle Ctrl+C
    ]

    # Reactive variables for UI updates
    status_message = reactive("Press 'Record' to begin.")
    elapsed_time_str = reactive("00:00:00")
    is_recording = reactive(False)
    is_paused = reactive(False)
    record_button_label = reactive("Record")
    record_button_variant = reactive("primary") # "primary" for Record, "warning" for Pause

    def __init__(self):
        super().__init__()
        global _app_instance_ref
        _app_instance_ref = self # Set the global reference
        self.start_time = 0.0
        self.output_filename = ""
        self._timer_update_elapsed = None # Textual timer object
        self.can_exit_now = False # To prevent premature exit before saving
        self._current_paused_duration = 0.0 # Accumulates paused time
        self._last_pause_time = 0.0
        self._exit_checker_timer = None # Timer for checking exit conditions
        self._is_saving_and_restarting = False # Flag to manage save & restart flow

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="header_info_container"):
            yield Static(self.status_message, id="status_display", classes="header_info_element")
            yield Static(self.elapsed_time_str, id="elapsed_display", classes="header_info_element")
        with Container(id="main_container"):
            with Horizontal(id="controls_container"):
                yield Button(self.record_button_label, id="record_pause_button", variant=self.record_button_variant, classes="control_element")
                yield Button("Save", id="save_button", variant="success", disabled=True, classes="control_element")
                yield Button("Discard", id="discard_button", variant="error", disabled=True, classes="control_element") # Added discard button
        yield Footer()

    def on_mount(self) -> None:
        """Called when app starts."""
        if not os.path.exists(OUTPUT_DIR):
            try:
                os.makedirs(OUTPUT_DIR)
            except OSError as e:
                self.status_message = f"Error creating output dir {OUTPUT_DIR}: {e}"
                # Disable buttons if output dir fails
                self.query_one("#record_pause_button", Button).disabled = True
                self.query_one("#save_button", Button).disabled = True
                return # Stop further mount processing

        self._update_record_pause_button_ui() # Set initial button states

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "record_pause_button":
            await self.action_toggle_record_pause()
        elif button_id == "save_button":
            await self.action_save_and_new_recording()
        elif button_id == "discard_button":
            self.action_discard_recording() # Discard doesn't need to be async

    def _update_record_pause_button_ui(self):
        """Updates the record/pause button label and variant based on state."""
        save_button = self.query_one("#save_button", Button)
        discard_button = self.query_one("#discard_button", Button)

        if not self.is_recording: # Idle or just saved
            self.record_button_label = "🔴 Record"
            self.record_button_variant = "primary" # Will style red in CSS
            save_button.disabled = True
            discard_button.disabled = True
        elif self.is_paused:
            self.record_button_label = "▶️ Resume"
            self.record_button_variant = "success" # Will style red in CSS
            save_button.disabled = False # Can still save when paused
            discard_button.disabled = False
        else: # Recording
            self.record_button_label = "⏸️ Pause"
            self.record_button_variant = "warning" # Will style red in CSS
            save_button.disabled = False
            discard_button.disabled = False
        
        # Update Save button label and variant (will style blue in CSS)
        save_button.label = "💾 Save"
        save_button.variant = "primary" # Will style blue in CSS

        # Update Discard button label and variant (will style red in CSS)
        discard_button.label = "🗑️ Discard"
        discard_button.variant = "error" # Will style red in CSS


    async def action_toggle_record_pause(self) -> None:
        """Handles Record/Pause/Resume button logic."""
        if not self.is_recording:
            # Start new recording
            self.start_recording_action()
        else:
            # Pause or Resume existing recording
            self.action_pause_resume_recording() # This existing method handles pause/resume logic
        self._update_record_pause_button_ui()


    async def action_save_and_new_recording(self) -> None:
        """Saves the current recording and immediately starts a new one."""
        if not self.is_recording or self._is_saving_and_restarting:
            return

        self._is_saving_and_restarting = True
        self.status_message = "Saving current recording..."
        self.query_one("#save_button", Button).disabled = True
        self.query_one("#record_pause_button", Button).disabled = True


        global stop_event, recording_thread, save_finished_event
        save_finished_event.clear()
        stop_event.set() # Signal current recording thread to stop and save

        # Wait for the recording thread to finish saving
        # This needs to be done carefully to avoid blocking the UI thread for too long
        # Using a timer to check the event
        await self.wait_for_save_completion()

        # Reset for new recording (after save is confirmed)
        if recording_thread and recording_thread.is_alive():
            recording_thread.join(timeout=1.0) # Give it a moment to exit

        # Start a new recording
        self.status_message = "Starting new recording..."
        self.start_recording_action() # This will set is_recording to True, etc.
        self._update_record_pause_button_ui()
        self.query_one("#record_pause_button", Button).disabled = False
        self._is_saving_and_restarting = False


    async def wait_for_save_completion(self):
        """Waits for save_finished_event to be set by the recording thread."""
        # This is a simplified wait. In a real app, you might use a worker or async task.
        while not save_finished_event.is_set():
            await asyncio.sleep(0.1) # Use asyncio.sleep for Textual async methods
        self.log("Save finished event received.")


    def start_recording_action(self):
        global recording_thread, stop_event, pause_event, audio_queue, save_finished_event

        if self.is_recording and not self._is_saving_and_restarting: # Prevent accidental restart if already recording
            return

        self.log("Starting recording action...")
        stop_event.clear()
        pause_event.clear()
        save_finished_event.clear() # Clear before new recording starts

        # Clear the queue for the new recording
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_filename = os.path.join(OUTPUT_DIR, f"recording_{timestamp}.wav")

        self.is_recording = True
        self.is_paused = False
        self.start_time = time.monotonic()
        self._current_paused_duration = 0.0
        self.can_exit_now = False # Reset for new recording
        self.update_elapsed_time() # Start/reset the timer
        self.status_message = "🔴 Recording..." # Initial status for new recording
        self._update_record_pause_button_ui() # Ensure buttons are updated

        recording_thread = threading.Thread(target=record_audio_thread, args=(self.output_filename, self))
        recording_thread.daemon = True
        recording_thread.start()
        self._update_record_pause_button_ui() # Update button states

    def update_elapsed_time(self) -> None:
        """Updates the elapsed time display."""
        if self.is_recording and not self.is_paused:
            current_elapsed = time.monotonic() - self.start_time - self._current_paused_duration
            duration = int(current_elapsed)
            hours, remainder = divmod(duration, 3600)
            minutes, seconds = divmod(remainder, 60)
            self.elapsed_time_str = f"{hours:02}:{minutes:02}:{seconds:02}"

        if self.is_recording:
            if self._timer_update_elapsed:
                self._timer_update_elapsed.reset() # Reset if already exists
            else:
                self._timer_update_elapsed = self.set_interval(1.0, self.update_elapsed_time)
        elif self._timer_update_elapsed:
             self._timer_update_elapsed.stop()
             self._timer_update_elapsed = None


    def action_quit_recording(self) -> None:
        """Stop recording (if any) and prepare to exit the app."""
        if self._is_saving_and_restarting:
            self.status_message = "Saving in progress, please wait to quit."
            return

        if self.is_recording:
            self.status_message = "Stopping and saving before exiting..."
            global stop_event, save_finished_event
            save_finished_event.clear() # Ensure it's clear if we are quitting
            stop_event.set() # Signal recording thread to stop and save

            if self._timer_update_elapsed:
                self._timer_update_elapsed.stop()
                self._timer_update_elapsed = None
            # The record_audio_thread will set can_exit_now = True
            # and save_finished_event.set()
            # We will use a periodic check to exit the app
            if self._exit_checker_timer:
                try:
                    self._exit_checker_timer.stop()
                except Exception: # Timer might already be stopped or invalid
                    pass
                self._exit_checker_timer = None
            # Start checking for exit conditions (save completion)
            self._exit_checker_timer = self.set_interval(0.2, self._check_exit_conditions_after_save)
        else:
            # If not recording, quit immediately
            self.exit()


    async def _check_exit_conditions_after_save(self):
        """Periodically check if saving is done (via save_finished_event) then exit."""
        if save_finished_event.is_set() or self.can_exit_now: # can_exit_now is a fallback
            if self._exit_checker_timer:
                try:
                    self._exit_checker_timer.stop()
                except Exception: pass
                self._exit_checker_timer = None
            self.log("Save finished, exiting application.")
            self.exit() # Textual's built-in quit

    async def _check_exit_conditions(self): # Original exit checker, might be redundant now
        """ Periodically check if we can exit. """
        if self.can_exit_now:
            if self._exit_checker_timer:
                try:
                    self._exit_checker_timer.stop()
                except Exception: # Timer might already be stopped or invalid
                    pass
                self._exit_checker_timer = None
            self.exit() # Textual's built-in quit

    def action_request_quit_app(self) -> None:
        """Handles Ctrl+C: stops recording if active, then exits."""
        if self.is_recording and not stop_event.is_set():
            self.status_message = "Ctrl+C pressed. Stopping and saving..."
            self.action_quit_recording() # This will initiate save and then exit check
        elif not self.is_recording and self.can_exit_now: # Already stopped, ready to exit
            self.exit()
        elif not self.is_recording and not self.can_exit_now and stop_event.is_set(): # Stopping in progress
            self.status_message = "Saving in progress. Please wait."
        else: # Not recording, not stopping, just exit
             self.exit()


    def action_discard_recording(self) -> None:
        """Discards the current recording."""
        if not self.is_recording:
            return

        self.log("Discarding recording...")
        global stop_event, recording_thread, audio_queue, save_finished_event
        
        # Signal the recording thread to stop, but without saving
        stop_event.set()
        # Clear the queue to ensure no data is saved
        while not audio_queue.empty():
            try:
                audio_queue.get_nowait()
            except queue.Empty:
                break
        
        # Wait briefly for the thread to acknowledge stop_event and exit
        if recording_thread and recording_thread.is_alive():
             recording_thread.join(timeout=1.0)

        # Reset state
        self.is_recording = False
        self.is_paused = False
        self.start_time = 0.0
        self._current_paused_duration = 0.0
        self.elapsed_time_str = "00:00:00"
        self.status_message = "Recording discarded."
        self.can_exit_now = True # Ready to exit if needed
        
        if self._timer_update_elapsed:
             self._timer_update_elapsed.stop()
             self._timer_update_elapsed = None

        # Clear events for next recording
        stop_event.clear()
        pause_event.clear()
        save_finished_event.clear()

        self._update_record_pause_button_ui() # Update button states


    def action_pause_resume_recording(self) -> None:
        """Toggle pause/resume state. Called by action_toggle_record_pause."""
        if not self.is_recording:
            return

        if self.is_paused: # Current state is paused, so RESUME
            pause_event.clear()
            self.is_paused = False
            self._current_paused_duration += (time.monotonic() - self._last_pause_time)
            self.status_message = "🔴 Recording..."
            self.update_elapsed_time() # Resume timer updates
        else: # Current state is recording, so PAUSE
            pause_event.set()
            self.is_paused = True
            self._last_pause_time = time.monotonic()
            self.status_message = "⏸️ Paused"
            # Timer update logic in update_elapsed_time already handles not advancing time if paused
        self._update_record_pause_button_ui()


    # Watch methods for reactive variables to update UI
    def watch_status_message(self, new_message: str) -> None:
        try:
            status_widget = self.query_one("#status_display", Static)
            status_widget.update(new_message)
        except Exception:
            pass # App might be shutting down

    def watch_elapsed_time_str(self, new_time_str: str) -> None:
        try:
            elapsed_widget = self.query_one("#elapsed_display", Static)
            elapsed_widget.update(new_time_str)
        except Exception:
            pass

    def watch_record_button_label(self, new_label: str) -> None:
        try:
            self.query_one("#record_pause_button", Button).label = new_label
        except Exception:
            pass # App might be shutting down

    def watch_record_button_variant(self, new_variant: str) -> None:
        try:
            self.query_one("#record_pause_button", Button).variant = new_variant
        except Exception:
            pass # App might be shutting down


    def set_status_from_callback(self, message: str):
        """Helper to update status message from audio callback thread."""
        self.status_message = message


if __name__ == "__main__":
    import asyncio # Required for await asyncio.sleep(0.1)
    # Pre-flight checks
    if not os.path.exists(OUTPUT_DIR):
        try:
            os.makedirs(OUTPUT_DIR)
            print(f"Created output directory: {OUTPUT_DIR}")
        except Exception as e:
            print(f"Error: Could not create output directory {OUTPUT_DIR}: {e}", file=sys.stderr)
            print("Please check permissions or create it manually.", file=sys.stderr)
            sys.exit(1)

    try:
        # Check if default microphone is available
        sd.check_input_settings(device=DEVICE, samplerate=SAMPLE_RATE, channels=CHANNELS)
    except Exception as e:
        print(f"Error initializing audio input: {e}", file=sys.stderr)
        print("Please ensure a microphone is connected and configured.", file=sys.stderr)
        print("Available devices:", file=sys.stderr)
        try:
            print(sd.query_devices(), file=sys.stderr)
        except Exception as dev_e:
            print(f"Could not query devices: {dev_e}", file=sys.stderr)
        sys.exit(1)

    app = MicRecApp()
    app.run(inline=True)

    # Clean up, though daemon thread should allow exit
    if recording_thread and recording_thread.is_alive():
        print("Main exit: Signaling recording thread to stop...", file=sys.stderr)
        stop_event.set()
        recording_thread.join(timeout=2) # Wait a bit for thread to finish
        if recording_thread.is_alive():
            print("Main exit: Recording thread still alive after timeout.", file=sys.stderr)
        else:
            print("Main exit: Recording thread finished.", file=sys.stderr)
    print("MicRec exited.", file=sys.stderr)