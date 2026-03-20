import os
import sys
import logging
import threading
import datetime
import warnings

warnings.simplefilter("ignore")

from textual.app import ComposeResult
from textual.widgets import TabbedContent, TabPane, RichLog, Header, Footer, Static, Button
from textual.containers import Container, Horizontal

from recorder.micrec import MicRecApp
from ws_client.client import WhisperClient

MD_PATH = os.getenv("MD_PATH", os.path.join(os.getcwd(), "data", "md"))


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
            pass


class WSUIApp(MicRecApp):
    CSS_PATH = "css/style.tcss"
    TITLE = "WS-UI - Record & Transcribe"

    BINDINGS = [
        ("w", "close_tab", "Close Tab"),
    ]

    def __init__(self):
        super().__init__()
        self._client = WhisperClient()
        self._pending_jobs: dict[int, str] = {}  # job_id -> filename
        self.results_map: dict[str, str] = {}
        self.closed_tab_ids: set[str] = set()
        self.session_start_time = datetime.datetime.utcnow()

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
                yield Button("Copy", id="copy_button", variant="primary", disabled=True, classes="control_element")
                yield Button("Save MD", id="save_md_button", variant="success", disabled=True, classes="control_element")
                yield Button("Clear Results", id="clear_results_button", variant="warning", classes="control_element")
                yield Button("Clear Logs", id="clear_logs_button", variant="warning", classes="control_element")

        with TabbedContent(id="results_tabs"):
            with TabPane("Logs", id="tab_logs"):
                yield RichLog(id="log_display", wrap=True)

        yield Footer()

    async def on_mount(self) -> None:
        super().on_mount()

        self._tui_log_handler = _TextualLogHandler(self)
        self._tui_log_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(self._tui_log_handler)
        if root_logger.level > logging.DEBUG:
            root_logger.setLevel(logging.DEBUG)

        # Poll for completed jobs every 2 seconds
        self.set_interval(2.0, self.check_for_completed_jobs)
        self.log_msg("WS-UI ready. Press Record to start.")

    async def action_save_and_new_recording(self) -> None:
        saved_filename = self.output_filename

        await super().action_save_and_new_recording()

        if saved_filename and os.path.exists(saved_filename):
            basename = os.path.basename(saved_filename)
            self.log_msg(f"Submitting job for {basename}...")
            try:
                result = self._client.submit_job(saved_filename)
                job_id = result["id"]
                self._pending_jobs[job_id] = basename
                self.log_msg(f"Job {job_id} registered for {basename}")
            except Exception as e:
                self.log_msg(f"Error submitting job: {e}")
        else:
            self.log_msg("Save failed or cancelled, skipping transcription.")

    def check_for_completed_jobs(self):
        """Poll the API for completed jobs."""
        for job_id in list(self._pending_jobs.keys()):
            try:
                job = self._client.get_job(job_id)
                job_status = job.get("status", "")
                if job_status == "completed":
                    basename = self._pending_jobs.pop(job_id)
                    content = job.get("md") or job.get("transcript") or "*No content found*"
                    safe_basename = basename.replace(".", "_").replace(" ", "_")
                    tab_id = f"tab_{safe_basename}"
                    if tab_id not in self.results_map and tab_id not in self.closed_tab_ids:
                        self.log_msg(f"Job {job_id} completed for {basename}")
                        self.add_result_tab(basename, content, tab_id)
                elif job_status == "failed":
                    basename = self._pending_jobs.pop(job_id)
                    self.log_msg(f"Job {job_id} failed for {basename}: {job.get('error_message', 'unknown error')}")
            except Exception:
                pass

    def add_result_tab(self, basename: str, text: str, tab_id: str):
        """Add a tab with transcription result."""
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
        """Close the currently active result tab."""
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
            for tab_id in list(self.results_map.keys()):
                try:
                    tabs.remove_pane(tab_id)
                    self.closed_tab_ids.add(tab_id)
                except Exception:
                    pass
            self.results_map.clear()
            try:
                tabs.active = "tab_logs"
            except Exception:
                pass
            self.query_one("#copy_button", Button).disabled = True
            self.query_one("#save_md_button", Button).disabled = True
            self.notify("All transcription results cleared.", title="Cleared")
        except Exception as e:
            self.notify(f"Error clearing results: {e}", title="Error", severity="error")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
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
                success = self.manual_copy_fallback(text)
                if success:
                    self.notify("Copied to clipboard!", title="Success", timeout=2.0)
                else:
                    self.notify("Failed to copy (missing xclip/wl-copy?)", title="Error", severity="error")
            else:
                self.notify("No transcription to copy.", title="Info")
        except Exception as e:
            self.notify(f"Failed to copy: {e}", title="Error", severity="error")

    def action_save_md(self):
        """Save the current transcription result to a Markdown file."""
        try:
            tabs = self.query_one(TabbedContent)
            active_id = tabs.active

            if active_id == "tab_logs" or active_id not in self.results_map:
                return

            text = self.results_map.get(active_id)
            if not text:
                self.notify("No content to save.", title="Info")
                return

            try:
                pane = self.query_one(f"#{active_id}", TabPane)
                filename_base = str(pane.title).strip()
            except Exception:
                filename_base = active_id.replace("tab_", "")

            if not filename_base.endswith(".md"):
                filename_base += ".md"

            os.makedirs(MD_PATH, exist_ok=True)
            full_path = os.path.join(MD_PATH, filename_base)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(text)

            self.notify(f"Saved to {filename_base}", title="Success")
            self.log_msg(f"Saved MD to {full_path}")

        except Exception as e:
            self.notify(f"Failed to save MD: {e}", title="Error", severity="error")
            self.log_msg(f"Error saving MD: {e}")

    def exit(self, *args, **kwargs):
        self._client.close()
        if self._session is not None:
            self._session.stop()
            self._session.join(timeout=2.0)
        if hasattr(self, '_tui_log_handler'):
            logging.getLogger().removeHandler(self._tui_log_handler)
        super().exit(*args, **kwargs)
