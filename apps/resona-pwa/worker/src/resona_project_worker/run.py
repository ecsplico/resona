"""Entry point: load config, then run the async poll loop forever."""
from __future__ import annotations

import asyncio
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from decouple import config

from .directus import DirectusClient
from .hub import HubClient
from .worker import run_once

log = logging.getLogger(__name__)


@dataclass
class Settings:
    directus_url: str
    directus_token: str
    resona_api_url: str
    resona_api_key: str
    engine: str
    poll_interval: int
    batch: int
    stale_minutes: int


def load_settings() -> Settings:
    token = config("DIRECTUS_TOKEN", default="")
    if not token:
        raise RuntimeError("DIRECTUS_TOKEN is required")
    api_key = config("RESONA_API_KEY", default="")
    if not api_key:
        raise RuntimeError("RESONA_API_KEY is required (hub client token)")
    return Settings(
        directus_url=config("DIRECTUS_URL", default="http://localhost:7700"),
        directus_token=token,
        resona_api_url=config("RESONA_API_URL", default="http://localhost:7000"),
        resona_api_key=api_key,
        engine=config("WORKER_ENGINE", default=""),
        poll_interval=config("WORKER_POLL_INTERVAL", default=5, cast=int),
        batch=config("WORKER_BATCH", default=5, cast=int),
        stale_minutes=config("WORKER_STALE_MINUTES", default=15, cast=int),
    )


async def _loop(settings: Settings) -> None:
    directus = DirectusClient(settings.directus_url, settings.directus_token)
    hub = HubClient(settings.resona_api_url, settings.resona_api_key)
    tmp_dir = Path(tempfile.mkdtemp(prefix="resona-project-worker-"))
    log.info("project-worker started (poll=%ss, batch=%s, engine=%s)",
             settings.poll_interval, settings.batch, settings.engine or "hub default")
    try:
        while True:
            try:
                n = await run_once(
                    directus, hub, tmp_dir=tmp_dir, batch=settings.batch,
                    stale_minutes=settings.stale_minutes, engine=settings.engine,
                )
                if n:
                    log.info("%s row(s) changed state", n)
            except Exception:
                log.exception("poll cycle failed; continuing")
            await asyncio.sleep(settings.poll_interval)
    finally:
        await directus.aclose()
        await hub.aclose()


def main() -> None:
    logging.basicConfig(
        level=config("LOGLEVEL", default="info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_loop(load_settings()))


if __name__ == "__main__":
    main()
