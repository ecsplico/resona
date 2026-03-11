import sys
import os
import logging
from dotenv import load_dotenv
import sounddevice as sd
from core.paths import FILE_PATH


def main():
    load_dotenv()

    # Suppress all root logging handlers that write to stderr — the TUI
    # installs its own handler in on_mount() so any StreamHandler here
    # would leak lines on top of the TUI.
    logging.root.handlers.clear()
    logging.root.addHandler(logging.NullHandler())

    OUTPUT_DIR = FILE_PATH
    SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 44100))
    CHANNELS = int(os.getenv("CHANNELS", 1))
    DEVICE = None

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

    from ws_live.ui import WSLiveApp
    app = WSLiveApp()
    app.run()


if __name__ == "__main__":
    main()
