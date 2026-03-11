import os

# --- Suppress C-level and Library Warnings to prevent TUI interference ---
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "3"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["HF_HUB_VERBOSITY"] = "error"

import sys
import logging
import threading
import queue
import time
import datetime
import warnings

# Filter Python warnings
warnings.simplefilter("ignore")

from textual.app import ComposeResult
from textual.widgets import TabbedContent, TabPane, RichLog, Header, Footer, Static, Button
from textual.containers import Container, Horizontal
from textual.message import Message
from recorder.micrec import MicRecApp

from core.db.engine import create_db_and_tables
from core.db.utils import register_job
from core.db.models import Job, JobStatus
from ws_server.processing.tasks_transcribe import TranscribeTask
from sqlmodel import Session, select
from core.db.engine import engine
from core.paths import MD_PATH


class _TextualLogHandler(logging.Handler):
    """Routes Python logging records into the TUI's log tab."""
    def __init__(self, app: 'WSUIApp'):
        super().__init__()
        self._app = app

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self._app.call_from_thread(self._app.log_msg, msg)
        except Exception:
            pass  # Don't let logging errors crash the app



class WSUIApp(MicRecApp):
    CSS_PATH = "css/style.tcss"
    TITLE = "🎤 WS-UI - Record & Transcribe"

    BINDINGS = [
        ("w", "close_tab", "Close Tab"),
    ]


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
                yield Button("💾 Save MD", id="save_md_button", variant="success", disabled=True, classes="control_element") # NEW: Save MD button
                yield Button("🗑️ Results", id="clear_results_button", variant="warning", classes="control_element")
                yield Button("🗑️ Logs", id="clear_logs_button", variant="warning", classes="control_element")

        # Bottom section
        with TabbedContent(id="results_tabs"):
            with TabPane("Logs", id="tab_logs"):
                yield RichLog(id="log_display", wrap=True)
        
        yield Footer()

    async def on_mount(self) -> None:
        super().on_mount()

        # Install a logging handler that routes to our log tab
        self._tui_log_handler = _TextualLogHandler(self)
        self._tui_log_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(self._tui_log_handler)
        # Capture all levels
        if root_logger.level > logging.DEBUG:
            root_logger.setLevel(logging.DEBUG)

        self.log_msg("Initializing database...")
        create_db_and_tables()
        
        self.log_msg("Starting TranscribeTask (background)...")
        self.transcription_task = TranscribeTask(self.stop_transcription_event)
        self.transcription_task.start()

        # Start a timer to poll for completed jobs
        self.set_interval(2.0, self.check_for_completed_jobs)

    async def action_save_and_new_recording(self) -> None:
        # Capture filename before it gets reset by super()
        saved_filename = self.output_filename
        
        # We need to wait for save to complete. super() uses wait_for_save_completion.
        await super().action_save_and_new_recording()
        
        if saved_filename and os.path.exists(saved_filename):
            basename = os.path.basename(saved_filename)
            self.log_msg(f"Registering job for {basename}...")
            # Register job in DB
            try:
                register_job(filename=basename, upload_name=basename, keep=True, translate=False)
                self.log_msg(f"Job registered for {basename}")
            except Exception as e:
                self.log_msg(f"Error registering job: {e}")
        else:
            self.log_msg("Save failed or cancelled (or no file), skipping transcription.")

    def check_for_completed_jobs(self):
        """Poll the database for completed jobs created after session start."""
        try:
            with Session(engine) as session:
                statement = (
                    select(Job)
                    .where(Job.status == JobStatus.COMPLETED)
                    .where(Job.created_at >= self.session_start_time)
                    .order_by(Job.updated_at.desc())
                    .limit(10)
                )
                jobs = session.exec(statement).all()
                
                for job in jobs:
                    basename = job.filename
                    safe_basename = basename.replace(".", "_").replace(" ", "_")
                    tab_id = f"tab_{safe_basename}"
                    
                    if tab_id not in self.results_map and tab_id not in self.closed_tab_ids:
                        self.log_msg(f"Found completed job {job.id} for {basename}")
                        content = job.md if job.md else "*No content found in DB*"
                        self.add_result_tab(basename, content, tab_id)

        except Exception:
            pass

    def add_result_tab(self, basename: str, text: str, tab_id: str):
        """Add a closable tab for the result."""
        try:
            tabs = self.query_one(TabbedContent)
            if tab_id in self.results_map:
                return

            self.results_map[tab_id] = text
            content = Static(text, classes="transcription_result", expand=True)
            pane = TabPane(basename, content, id=tab_id)
            tabs.add_pane(pane)
            tabs.active = tab_id
        except Exception as e:
            self.log_msg(f"Error adding tab: {e}")

    def action_close_tab(self) -> None:
        """Close the currently active result tab (not the Logs tab)."""
        try:
            tabs = self.query_one(TabbedContent)
            active_id = tabs.active
            if active_id == "tab_logs":
                self.notify("Cannot close the Logs tab.", title="Info")
                return
            if active_id in self.results_map:
                del self.results_map[active_id]
                self.closed_tab_ids.add(active_id)
                tabs.remove_pane(active_id)
                self.notify("Tab closed.", title="Closed", timeout=1.5)
        except Exception as e:
            self.log_msg(f"Error closing tab: {e}")

    def __init__(self):
        super().__init__()
        self.stop_transcription_event = threading.Event()
        self.transcription_task = None
        self.results_map = {} # Map tab ID to text content
        self.closed_tab_ids = set() # Track closed tabs so they don't reappear
        self.session_start_time = datetime.datetime.utcnow()  # Only show jobs after this

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
        elif button_id == "save_md_button":
            self.action_save_md()
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
                    self.closed_tab_ids.add(tab_id)
                except Exception:
                    pass # Maybe already removed
            
            self.results_map.clear()
            
            # Switch back to Logs if available
            try:
                tabs.active = "tab_logs"
            except Exception:
                pass
            
            # Update Copy/Save buttons (will likely be disabled by on_tabbed_content_tab_activated)
            self.query_one("#copy_button", Button).disabled = True
            self.query_one("#save_md_button", Button).disabled = True
            
            self.notify("All transcription results cleared.", title="Cleared")
        except Exception as e:
            self.notify(f"Error clearing results: {e}", title="Error", severity="error")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Handle tab switching to update Copy/Save button state."""
        try:
            active_id = event.pane.id
            copy_btn = self.query_one("#copy_button", Button)
            save_md_btn = self.query_one("#save_md_button", Button)
            
            if active_id == "tab_logs":
                copy_btn.disabled = True
                save_md_btn.disabled = True
            elif active_id in self.results_map:
                copy_btn.disabled = False
                save_md_btn.disabled = False
            else:
                copy_btn.disabled = True
                save_md_btn.disabled = True
        except Exception:
            pass

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
    
    def action_save_md(self):
        """Save the current transcription result to a Markdown file in MD_PATH."""
        try:
            tabs = self.query_one(TabbedContent)
            active_id = tabs.active
            
            if active_id == "tab_logs" or active_id not in self.results_map:
                return

            text = self.results_map.get(active_id)
            if not text:
                self.notify("No content to save.", title="Info")
                return

            # Get the pane (and label) to determine filename
            try:
                # Need to iterate or find the pane by ID. TabbedContent methods aren't exhaustive for lookup.
                # However, tabs.get_pane(active_id) exists in recent Textual versions.
                # If not, we might need traverse children.
                # Actually, TabPane has id=active_id.
                pane = self.query_one(f"#{active_id}", TabPane)
                # The label is cleaner but strictly speaking it's a Text/Renderable.
                # Usually pane.title or label exists. In older Textual it's `title`.
                # Wait, add_pane(pane) uses pane(title, content).
                # So pane.title is the basename.
                # Let's clean it up to be safe.
                filename_base = str(pane.title).strip()
            except Exception:
                # Fallback if query fails
                filename_base = active_id.replace("tab_", "")
            
            if not filename_base.endswith(".md"):
                filename_base += ".md"
            
            # Ensure MD path exists
            os.makedirs(MD_PATH, exist_ok=True)
            
            full_path = os.path.join(MD_PATH, filename_base)
            
            self.log_msg(f"Saving processing result to {full_path}")
            
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(text)
                
            self.notify(f"Saved to {filename_base}", title="Success")
            
        except Exception as e:
            self.notify(f"Failed to save MD: {e}", title="Error", severity="error")
            self.log_msg(f"Error saving MD: {e}")
            
    def exit(self, *args, **kwargs):
        self.stop_transcription_event.set()
        if self.transcription_task and self.transcription_task.is_alive():
            self.transcription_task.join(timeout=2.0)
        # Ensure recording session thread is joined
        if self._session is not None:
            self._session.stop()
            self._session.join(timeout=2.0)
        if hasattr(self, '_tui_log_handler'):
            logging.getLogger().removeHandler(self._tui_log_handler)
        super().exit(*args, **kwargs)
