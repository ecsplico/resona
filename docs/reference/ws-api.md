# resona-api Internals

Internal reference for `resona-api` — the job queue and postprocessing service.

!!! note
    The public-facing REST interface is documented in the [README](../index.md) and exposed via the OpenAPI UI at `http://localhost:7000/docs` when the service is running.

!!! note "Legacy"
    The older `ws-api` package is retained for backward compatibility. This page documents `resona-api` (`packages/api/`). For the legacy internals, the module paths are `ws_api.*` instead of `resona_api.*`.

## Database models

::: resona_api.db.models.JobStatus

::: resona_api.db.models.Job

::: resona_api.db.models.Replacement

::: resona_api.db.models.InitialPrompt

## Engine client

::: resona_api.engine_client.EngineClient

## Background task

::: resona_api.tasks_transcribe.TranscribeTask

## Database utilities

::: resona_api.db.utils.register_job

::: resona_api.db.utils.get_active_replacements

::: resona_api.db.utils.get_active_initial_prompts_string
