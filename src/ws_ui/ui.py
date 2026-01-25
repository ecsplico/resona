import os
import threading
import queue
import time
from textual.app import ComposeResult
from textual.widgets import TabbedContent, TabPane, RichLog, Header, Footer, Static, Button
from textual.containers import Container, Horizontal
from textual.message import Message
from recorder.micrec import MicRecApp
class TranscriptionFinished(Message):
    """Message sent when transcription is finished."""
    def __init__(self, filename: str, result: dict) -> None:
        self.filename = filename
        self.result = result
        super().__init__()

class WSUIApp(MicRecApp):
    CSS_PATH = "css/style.tcss"
    TITLE = "🎤 WS-UI - Record & Transcribe"


    def compose(self) -> ComposeResult:
        yield Header()
        
        # Top section (Copied structure from MicRecApp but adjusted for dock/layout via CSS)
        with Horizontal(id="header_info_container"):
            yield Static(self.status_message, id="status_display", classes="header_info_element")
            yield Static(self.elapsed_time_str, id="elapsed_display", classes="header_info_element")
        
        with Container(id="main_container"):
            with Horizontal(id="controls_container"):
                yield Button(self.record_button_label, id="record_pause_button", variant=self.record_button_variant, classes="control_element")
                yield Button("Save", id="save_button", variant="success", disabled=True, classes="control_element")
                yield Button("Discard", id="discard_button", variant="error", disabled=True, classes="control_element")
                yield Button("📋 Copy", id="copy_button", variant="primary", disabled=True, classes="control_element") # Global copy button
                yield Button("🗑️ Results", id="clear_results_button", variant="warning", classes="control_element")
                yield Button("🗑️ Logs", id="clear_logs_button", variant="warning", classes="control_element")

        # Bottom section
        with TabbedContent(id="results_tabs"):
            with TabPane("Logs", id="tab_logs"):
                yield RichLog(id="log_display", wrap=True)
        
        yield Footer()

    async def on_mount(self) -> None:
        super().on_mount()
        self.log_msg("Initializing Transcriber (loading model in background)...")
        self.transcription_thread = threading.Thread(target=self.transcription_worker, daemon=True)
        self.transcription_thread.start()

    async def action_save_and_new_recording(self) -> None:
        # Capture filename before it gets reset by super()
        saved_filename = self.output_filename
        
        # We need to wait for save to complete. super() uses wait_for_save_completion.
        await super().action_save_and_new_recording()
        
        if saved_filename and os.path.exists(saved_filename):
            self.log_msg(f"Queuing {os.path.basename(saved_filename)} for transcription...")
            self.transcribe_queue.put(saved_filename)
        else:
            self.log_msg("Save failed or cancelled (or no file), skipping transcription.")

    def log_msg(self, msg: str) -> None:
        """Write to the log tab."""
        try:
            log_display = self.query_one("#log_display", RichLog)
            timestamp = time.strftime("%H:%M:%S")
            log_display.write(f"[{timestamp}] {msg}")
        except Exception:
            pass

    def transcription_worker(self):
        self.call_from_thread(self.log_msg, "Worker thread started.")
        try:
            from ws_server.processing.transcriber_factory import getTranscriber
            import whisper
            
            # Check environment or config availability
            self.call_from_thread(self.log_msg, "Initializing Transcriber (checking .env for ASR_MODE)...")
            self.transcriber = getTranscriber()
            self.call_from_thread(self.log_msg, f"Transcriber loaded: {self.transcriber.modelname}")
        except Exception as e:
            self.call_from_thread(self.log_msg, f"Error loading transcriber: {e}")
            import traceback
            traceback.print_exc()
            return

        while not self.stop_transcription_event.is_set():
            try:
                filename = self.transcribe_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            basename = os.path.basename(filename)
            self.call_from_thread(self.log_msg, f"Processing {basename}...")
            
            try:
                # Transcribe
                start_t = time.time()
                
                # Load audio using openai-whisper utilities to ensure np.ndarray compatible with all transcribers
                self.call_from_thread(self.log_msg, f"Loading audio {basename}...")
                audio = whisper.load_audio(filename)
                
                self.call_from_thread(self.log_msg, f"Transcribing {basename} with {type(self.transcriber).__name__}...")
                result = self.transcriber.transcribe(audio, language="de") 
                duration = time.time() - start_t
                
                self.call_from_thread(self.log_msg, f"Transcription finished for {basename} ({duration:.2f}s)")
                self.post_message(TranscriptionFinished(filename, result))
                
            except Exception as e:
                self.call_from_thread(self.log_msg, f"Error transcribing {basename}: {e}")
                import traceback
                traceback.print_exc()

    def __init__(self):
        super().__init__()
        self.transcribe_queue = queue.Queue()
        self.transcriber = None
        self.stop_transcription_event = threading.Event()
        self.transcription_thread = None
        self.results_map = {} # Map tab ID to text content

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop() # Stop propagation to prevent double-handling
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
            self.action_clear_results()
        elif button_id == "clear_logs_button":
            self.action_clear_logs()

    def action_clear_results(self):
        """Remove all transcription result tabs."""
        try:
            tabs = self.query_one(TabbedContent)
            # Iterate over a copy of keys to avoid modification during iteration
            for tab_id in list(self.results_map.keys()):
                try:
                    tabs.remove_pane(tab_id)
                except Exception:
                    pass # Maybe already removed
            
            self.results_map.clear()
            
            # Switch back to Logs if available
            try:
                tabs.active = "tab_logs"
            except Exception:
                pass
            
            # Update Copy button (will likely be disabled by on_tabbed_content_tab_activated)
            self.query_one("#copy_button", Button).disabled = True
            
            self.notify("All transcription results cleared.", title="Cleared")
        except Exception as e:
            self.notify(f"Error clearing results: {e}", title="Error", severity="error")

    def action_clear_logs(self):
        """Clear the log display."""
        try:
            log_display = self.query_one("#log_display", RichLog)
            log_display.clear()
            self.notify("Logs cleared.", title="Cleared")
        except Exception as e:
            self.notify(f"Error clearing logs: {e}", title="Error", severity="error")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle tab switching to update Copy button state."""
        try:
            active_id = event.pane.id
            copy_btn = self.query_one("#copy_button", Button)
            
            if active_id == "tab_logs":
                copy_btn.disabled = True
            elif active_id in self.results_map:
                copy_btn.disabled = False
            else:
                copy_btn.disabled = True
        except Exception:
            pass

    def manual_copy_fallback(self, text: str) -> bool:
        """Try to copy to clipboard using system tools (xclip, xsel, wl-copy)."""
        import subprocess
        import shutil

        # List of commands to try: (command, args)
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
        try:
            tabs = self.query_one(TabbedContent)
            active_id = tabs.active
            text = self.results_map.get(active_id)
            if text:
                success = False
                # Try Textual's built-in first
                try:
                    self.app.copy_to_clipboard(text)
                    success = True
                except Exception:
                    pass
                
                # If that didn't work (or we want to be sure on linux), try fallback
                # Textual's copy_to_clipboard often works seamlessly but sometimes fails on specific Linux setups.
                # Use fallback if success is checking return (it wraps pyperclip usually, but let's be safe)
                if not success or True: # Force fallback check for Linux if needed?
                     # Actually Textual might not return success status easily.
                     # Let's try our fallback explicitly if on Linux and user reported issues.
                     if self.manual_copy_fallback(text):
                         success = True

                if success:
                    self.notify("Copied to clipboard!", title="Success", timeout=2.0)
                else:
                    self.notify("Failed to copy (missing xclip/wl-copy?)", title="Error", severity="error")
            else:
                self.notify("No transcription to copy.", title="Info")
        except Exception as e:
            self.notify(f"Failed to copy: {e}", title="Error", severity="error")
    
    # Debounce control
    _last_toggle_time = 0.0

    async def action_toggle_record_pause(self) -> None:
        """Override with debounce to prevent double-triggering."""
        now = time.monotonic()
        if now - self._last_toggle_time < 0.5: # 500ms debounce
            self.log_msg("Debounced toggle action (too fast).")
            return
        self._last_toggle_time = now

        self.log_msg(f"Toggle Record/Pause. Current: Recording={self.is_recording}, Paused={self.is_paused}")
        
        await super().action_toggle_record_pause()

    def on_transcription_finished(self, message: TranscriptionFinished) -> None:
        try:
            basename = os.path.basename(message.filename)
            text = message.result.get("text", "")
            
            tabs = self.query_one(TabbedContent)
            
            # Use basename based ID to prevent duplicates
            safe_basename = basename.replace(".", "_").replace(" ", "_")
            tab_id = f"tab_{safe_basename}"
            
            # Check if tab already exists
            # We can check if we have text for it
            if tab_id in self.results_map:
                self.log_msg(f"Tab for {basename} already exists, updating text.")
                # Update text and switch?
                # For now just return or update. Let's update map and maybe content if we could query it easily.
                self.results_map[tab_id] = text 
                # If we wanted to update content we'd need to find the Static widget.
                # simpler: just ignore or delete old? 
                # Let's just return to avoid visual duplicate, assuming it's same content.
                return

            # Store text for this tab
            self.results_map[tab_id] = text

            # Create layout
            content = Static(text, classes="transcription_result", expand=True)
            
            # Just content in the pane now, no internal toolbar
            pane = TabPane(basename, content, id=tab_id)
            tabs.add_pane(pane)
            
            # Switch to the new tab
            tabs.active = tab_id
            
            # Manually trigger button update because switching tab might happen before event propagates?
            # Or reliance on on_tabbed_content_tab_activated is enough.
            
        except Exception as e:
            self.log_msg(f"Error updating UI: {e}")
            import traceback
            traceback.print_exc()

    # Ensure clean exit
    def exit(self, *args, **kwargs):
        self.stop_transcription_event.set()
        if self.transcription_thread and self.transcription_thread.is_alive():
            self.transcription_thread.join(timeout=1.0)
        super().exit(*args, **kwargs)
