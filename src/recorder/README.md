# MicRec - CLI Audio Recorder

`micrec` is a command-line application to record audio from the default microphone.
It provides a simple Textual User Interface (TUI) to display status and control recording.

## Features

- **Audio Recording**: Captures audio from the local default microphone.
- **Stop Control**: Press 'Q' to stop recording and save the audio file.
- **Pause/Unpause Control**: Press 'P' to pause and resume recording.
- **Configuration**: The output directory for saved recordings is read from a `.env` file.
- **File Saving**: Audio is saved as a `.wav` file with a timestamped name (e.g., `recording_YYYYMMDD_HHMMSS.wav`).
- **Textual UI**: Displays recording status (Recording, Paused, Stopped), elapsed time, and key bindings.

## Installation

1.  **Clone the repository or download the `src/recorder` directory.**

2.  **Navigate to the `src/recorder` directory:**
    ```bash
    cd path/to/your_project/src/recorder
    ```

3.  **Create a Python virtual environment (recommended):**
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows use: .venv\Scripts\activate
    ```

4.  **Install dependencies using `uv` (or `pip`):**
    The project uses `uv` and a `pyproject.toml` file (located in the parent directory `whisper_server`) for dependency management. If you are running `micrec.py` standalone from within the `src/recorder` directory and have `uv` installed, you might need to ensure the dependencies are recognized or install them directly.

    The required dependencies are:
    - `textual`
    - `sounddevice`
    - `soundfile`
    - `numpy`
    - `python-dotenv`

    If you have `uv` installed and are in the main project root (`whisper_server`):
    ```bash
    uv pip install textual sounddevice soundfile numpy python-dotenv
    ```
    Or, if you have added them to the main `pyproject.toml` as instructed, `uv sync` in the project root should install them.

    If you are treating `src/recorder` as a standalone project and don't want to use the parent `pyproject.toml`, you would typically have a local `pyproject.toml` or `requirements.txt` here. For simplicity, assuming dependencies are managed at the project root or installed directly:
    ```bash
    # Ensure uv is installed: pip install uv
    # Then, from within src/recorder:
    uv pip install textual sounddevice soundfile numpy python-dotenv
    ```

5.  **Configure the Output Directory:**
    Create a file named `.env` in the `src/recorder` directory with the following content:
    ```ini
    OUTPUT_DIR="~/AudioRecordings"
    ```
    - You can change `~/AudioRecordings` to your preferred directory. `~` will be expanded to your home directory.
    - If `OUTPUT_DIR` is not specified or the `.env` file is missing, recordings will be saved to a `micrec_recordings` subdirectory within the `src/recorder` directory.
    - Optional configurations (add to `.env` if needed):
      ```ini
      # SAMPLE_RATE=44100
      # CHANNELS=1
      ```

## Usage

1.  **Ensure your microphone is connected and configured as the default input device for your system.**

2.  **Run the application from the `src/recorder` directory:**
    ```bash
    python micrec.py
    ```

3.  **The Textual UI will appear:**
    ```
    +---------------------------------------------------+
    | 🎤 MicRec - CLI Audio Recorder                    |
    +---------------------------------------------------+
    |                                                   |
    | Status:                                           |
    | [Press START to begin recording.]                 |
    |                                                   |
    | Elapsed Time:                                     |
    | [00:00:00]                                        |
    |                                                   |
    | [ Start Recording ]                               |
    |                                                   |
    +---------------------------------------------------+
    | Quit (Q) | Pause/Resume (P)                       |
    +---------------------------------------------------+
    ```

4.  **Controls:**
    -   **Start Recording**: Click the "Start Recording" button.
    -   **Pause/Resume**: Press 'P'. The status will change to "⏸️ Paused" or "🔴 Recording...".
    -   **Stop & Save**: Press 'Q'. The recording will be saved, and the status will show "✅ Saved: recording_YYYYMMDD_HHMMSS.wav". The application will then exit.
    -   **Force Quit**: Press `Ctrl+C`. If a recording is in progress, it will attempt to stop and save it before exiting.

## Troubleshooting

-   **"Error initializing audio input"**:
    -   Ensure a microphone is connected and selected as the default input device in your operating system's sound settings.
    -   The application will attempt to list available audio devices if an error occurs.
-   **"Error creating output directory"**:
    -   Check if you have write permissions for the specified `OUTPUT_DIR` or the `src/recorder` directory (if `OUTPUT_DIR` is not set).
-   **Dependencies not found**:
    -   Make sure you have activated your virtual environment and installed the required packages as listed in the "Installation" section.

## How it Works

-   **Python**: Core language.
-   **Textual**: For the TUI.
-   **sounddevice**: For microphone input.
-   **soundfile**: For saving audio to `.wav` files.
-   **numpy**: Dependency for audio data manipulation.
-   **python-dotenv**: For loading configuration from the `.env` file.
-   **threading**: Audio recording runs in a separate thread to keep the UI responsive.
-   **queue**: Used to pass audio data from the recording thread to the main thread for saving.

## Future Enhancements (Ideas)

-   Select microphone from a list within the UI.
-   Visual audio level meter (VU meter).
-   Configuration for more audio parameters (e.g., bit depth).
-   Option to choose output file format (e.g., MP3, FLAC - would require additional libraries like LAME).
-   More robust key binding configuration.
-   A simple file browser to view past recordings.