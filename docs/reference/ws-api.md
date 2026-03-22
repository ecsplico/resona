# ws-api Internals

Internal reference for `ws-api` — the job queue service.

!!! note
    The public-facing REST interface is documented in the [README](../index.md) and exposed via the OpenAPI UI at `http://localhost:7000/docs` when the service is running.

## Database models

::: ws_api.db.models.JobStatus

::: ws_api.db.models.Job

::: ws_api.db.models.Replacement

::: ws_api.db.models.InitialPrompt

## Engine client

::: ws_api.engine_client.EngineClient

## Background task

::: ws_api.tasks_transcribe.TranscribeTask

## Database utilities

::: ws_api.db.utils.register_job

::: ws_api.db.utils.get_active_replacements

::: ws_api.db.utils.get_active_initial_prompts_string
