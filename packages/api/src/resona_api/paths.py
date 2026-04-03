import os
from pathlib import Path


def _resolve(env_key: str, default_rel: str, base: Path) -> Path:
    """Resolve a path from env or a relative default; create the directory."""
    val = os.getenv(env_key)
    if val:
        p = Path(val)
        p = p if p.is_absolute() else Path.cwd() / p
    else:
        p = base / default_rel
    p.mkdir(parents=True, exist_ok=True)
    return p


DATA_PATH: Path = _resolve("DATA_PATH", "data", Path.cwd())
FILE_PATH: Path = _resolve("FILE_PATH", "files", DATA_PATH)
MD_PATH: Path = _resolve("MD_PATH", "md", DATA_PATH)
DB_PATH: Path = _resolve("DB_PATH", "db", DATA_PATH)

DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH / 'jjobs.sqlite'}")
