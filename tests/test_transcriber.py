import os
import sys
from dotenv import load_dotenv
load_dotenv()

try:
    from ws_server.processing.transcriber_fast_whisper import FastWhisperTranscriber
    print("Import successful")
except ImportError as e:
    print(f"Import failed: {e}")
    sys.exit(1)

print("Initializing Transcriber...")
try:
    transcriber = FastWhisperTranscriber()
    print(f"Initialized: {transcriber.modelname}")
except Exception as e:
    print(f"Initialization failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# we can skip actual transcription if init works, or test with a dummy file if needed.
print("Test complete.")
