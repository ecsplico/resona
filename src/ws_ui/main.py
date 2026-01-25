import sys
import os
from dotenv import load_dotenv
import sounddevice as sd
from ws_ui.ui import WSUIApp
from core.paths import INBOX_PATH

def main():
    load_dotenv()
    
    # Pre-flight checks
    OUTPUT_DIR = INBOX_PATH
    SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 44100))
    CHANNELS = int(os.getenv("CHANNELS", 1))
    DEVICE = None

    if not os.path.exists(OUTPUT_DIR):
        try:
            os.makedirs(OUTPUT_DIR)
        except Exception as e:
            print(f"Error: Could not create output directory {OUTPUT_DIR}: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        sd.check_input_settings(device=DEVICE, samplerate=SAMPLE_RATE, channels=CHANNELS)
    except Exception as e:
        print(f"Error initializing audio input: {e}", file=sys.stderr)
        sys.exit(1)

    app = WSUIApp()
    app.run()

if __name__ == "__main__":
    main()
