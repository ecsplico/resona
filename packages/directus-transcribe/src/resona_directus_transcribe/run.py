"""Entry point: load config, then run the async poll loop forever."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from decouple import config

from .client import DirectusClient
from .transcribe import TranscribeClient
from .worker import run_once

log = logging.getLogger(__name__)


@dataclass
class Settings:
    directus_url: str
    directus_token: str
    resona_api_url: str
    resona_api_key: str
    poll_interval: int
    concurrency: int
    stale_minutes: int


def load_settings() -> Settings:
    token = config("DIRECTUS_TOKEN", default="")
    if not token:
        raise RuntimeError("DIRECTUS_TOKEN is required")
    return Settings(
        directus_url=config("DIRECTUS_URL", default="http://localhost:7700"),
        directus_token=token,
        resona_api_url=config("RESONA_API_URL", default="http://localhost:7710"),
        resona_api_key=config("RESONA_API_KEY", default=""),
        poll_interval=config("TRANSCRIBE_POLL_INTERVAL", default=5, cast=int),
        concurrency=config("TRANSCRIBE_CONCURRENCY", default=2, cast=int),
        stale_minutes=config("TRANSCRIBE_STALE_MINUTES", default=15, cast=int),
    )


async def _loop(settings: Settings) -> None:
    directus = DirectusClient(settings.directus_url, settings.directus_token)
    transcribe = TranscribeClient(settings.resona_api_url, settings.resona_api_key)
    tmp_dir = Path(tempfile.mkdtemp(prefix="resona-transcribe-"))
    log.info("directus-transcribe worker started (poll=%ss, concurrency=%s)",
             settings.poll_interval, settings.concurrency)
    try:
        while True:
            try:
                n = await run_once(
                    directus, transcribe, tmp_dir=tmp_dir,
                    concurrency=settings.concurrency, stale_minutes=settings.stale_minutes,
                )
                if n:
                    log.info("processed %s recording(s)", n)
            except Exception:
                log.exception("poll cycle failed; continuing")
            await asyncio.sleep(settings.poll_interval)
    finally:
        await directus.aclose()
        await transcribe.aclose()


def main() -> None:
    logging.basicConfig(
        level=config("LOGLEVEL", default="info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_loop(load_settings()))


if __name__ == "__main__":
    main()
