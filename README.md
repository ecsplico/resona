# Whisper Server - ASR Service

A secure, production-ready API for Automatic Speech Recognition (ASR) using OpenAI's Whisper models. Features API key authentication, job status tracking, and both synchronous and asynchronous processing.

## Quick Start

1. **Install dependencies**:
   ```bash
   uv sync
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env and set your API_KEY
   ```

3. **Run the server**:
   ```bash
   uv run python run.py
   ```

## Security

### API Authentication

All API endpoints require authentication using an API key. Set your API key in the `.env` file:

```bash
API_KEY=your_secure_api_key_here
```

Include the API key in all requests using the `X-API-Key` header:

```bash
curl -H "X-API-Key: your_api_key" http://localhost:8000/jobs/
```

## Project Structure

```
.
├── .dockerignore
├── .env
├── .gitignore
├── .python-version
├── Dockerfile
├── Dockerfile.gpu
├── pyproject.toml
├── README.md           # This file
├── run.py              # Entry point for the application
├── uv.lock
├── src/                # Source code directory
│   ├── __init__.py
│   ├── app.py          # FastAPI application
│   ├── BackgroundTasks.py # Background tasks for processing audio files
│   ├── model.py        # SQLModel definitions for database interaction
│   ├── paths.py        # Path definitions
│   ├── replacements.py # Text replacements for markdown conversion
│   └── utils.py        # Utility functions for ASR and audio processing
├── tests/              # Test directory
│   └── test.http       # Example HTTP test file
└── webapp/             # Static files for the web application
```

## Modules

### `src/app.py`

This file defines the FastAPI application and its endpoints. It handles:

*   Serving static files for the web application.
*   Defining API endpoints for synchronous and asynchronous ASR processing.
*   Registering audio files for processing.
*   Retrieving job results.

Key components:

*   `lifespan`:  An async context manager that starts and stops background tasks (`TranscribeTask` and `ScanInboxTask`).
*   `asr`: An endpoint for synchronous ASR processing.
*   `transcribe_async`: An endpoint for asynchronous ASR processing.
*   `registerfile`: An endpoint to register an existing file for ASR processing.
*   `get_async`: An endpoint to retrieve the result of an asynchronous ASR job.
*   `get_jobs`: An endpoint to retrieve all ASR jobs.

### `src/BackgroundTasks.py`

This file defines the background tasks responsible for scanning the inbox directory and transcribing audio files.

Key components:

*   `ScanInboxTask`: A thread that scans the inbox directory for new audio files and registers them as jobs in the database.
*   `TranscribeTask`: A thread that retrieves jobs from the database and performs ASR processing using the `run_asr` function.

### `src/model.py`

This file defines the SQLModel models for interacting with the database.

Key components:

*   `Job`: A model representing an ASR job, including information about the file, processing status, transcript, and other metadata.
*   `Replacement`: A model representing a text replacement rule for markdown conversion.

### `src/utils.py`

This file defines utility functions for ASR processing, audio loading, and result writing.

Key components:

*   `WhisperTranscriber`, `TransformerTranscriber`, `FastWhisperTranscriber`: Classes that implement different ASR models.
*   `getTranscriber`: A function that returns an instance of the configured ASR model.
*   `run_asr`: A function that performs ASR processing on an audio file.
*   `load_audio`: A function that loads an audio file and converts it to a NumPy array.
*   `write_result`: A function that writes the ASR result to a file in the specified format.
*   `toMarkdown`: A function that converts the ASR transcript to markdown, applying text replacement rules from the database.
*   `register_job`: A function that registers a job in the database.

## Features

- **🔒 Secure**: API key authentication on all endpoints
- **📊 Job Tracking**: Detailed status tracking (PENDING, PROCESSING, COMPLETED, FAILED)
- **⚡ Async Processing**: Upload files and retrieve results later
- **🔍 Error Tracking**: Detailed error messages stored for failed jobs
- **🛡️ Input Validation**: Filename sanitization and file type validation
- **⏱️ Timestamps**: Automatic created_at and updated_at tracking

## Job Status Lifecycle

Jobs progress through the following states:

1. **PENDING**: Job created, waiting to be processed
2. **PROCESSING**: Currently being transcribed
3. **COMPLETED**: Successfully transcribed, results available
4. **FAILED**: Error occurred, check `error_message` field

## Usage

1.  **Upload an audio file** to the `/asr-async` endpoint for asynchronous processing
2.  **Retrieve the job ID** from the response
3.  **Poll `/job/{id}`** to check status and retrieve results when completed

## Endpoints

**Note**: All endpoints require the `X-API-Key` header.

*   **/asr**: Processes an audio file synchronously and returns the transcription.
    *   Method: POST
    *   Headers: `X-API-Key: <your_api_key>`
    *   Request body: `audio_file` (audio file to transcribe)
    *   Query parameters:
        *   `encode` (boolean, default: True): Encode audio first through ffmpeg
        *   `task` (string, default: "transcribe", enum: ["transcribe", "translate"]): Task to perform (transcribe or translate)
        *   `language` (string, default: None): Language of the audio file
        *   `initial_prompt` (string, default: None): Initial prompt for the ASR model
        *   `vad_filter` (boolean, default: False): Enable voice activity detection (VAD)
        *   `word_timestamps` (boolean, default: False): Word level timestamps
        *   `markdown` (boolean, default: True): Convert the result to markdown
        *   `output` (string, default: "txt", enum: ["txt", "vtt", "srt", "tsv", "json"]): Output format
    *   Response: StreamingResponse containing the transcription in the specified format.
*   **/asr-async**: Processes audio files asynchronously and returns job IDs.
    *   Method: POST
    *   Headers: `X-API-Key: <your_api_key>`
    *   Request body: `audio_files` (list of audio files to transcribe)
    *   Form parameters:
        *   `keep` (boolean, default: True): Keep the audio file after processing
        *   `translate` (boolean, default: False): Translate the transcription to English
    *   Response: JSON array containing job objects with `id`, `file`, and `result` URLs.
*   **/asr-registerfile**: Registers an existing file for processing.
    *   Method: POST
    *   Headers: `X-API-Key: <your_api_key>`
    *   Request body: `filename` (string, the name of the file to register)
    *   Response: JSON object containing job information.
*   **/job/{id}**: Retrieves the result of an asynchronous processing job.
    *   Method: GET
    *   Headers: `X-API-Key: <your_api_key>`
    *   Parameters: `id` (integer, the ID of the job)
    *   Response: JSON object containing the job details, including status, transcription, and error_message if failed.
*   **/jobs**: Retrieves all jobs.
    *   Method: GET
    *   Headers: `X-API-Key: <your_api_key>`
    *   Response: JSON array containing all job objects.

## Testing

Run the test suite:

```bash
uv run pytest
```

Run specific test files:

```bash
uv run pytest tests/test_api.py
uv run pytest tests/test_security.py
```