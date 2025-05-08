from pathlib import Path
from decouple import config, UndefinedValueError

# Determine the project root directory.
# __file__ is src/core/paths.py -> .parent is src/core -> .parent.parent is src -> .parent.parent.parent is project root.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent

def _resolve_path(
    env_var_key: str,
    default_relative_to_base: str,
    base_path_for_default: Path,
) -> Path:
    """
    Resolves a path based on an environment variable or a default.
    - If env_var_key is set:
        - If absolute, it's used directly.
        - If relative, it's resolved against project_root_for_env_relative.
    - If env_var_key is not set, default_relative_to_base is resolved against base_path_for_default.
    """
    try:
        path_str = config(env_var_key)
        configured_path = Path(path_str)
        if configured_path.is_absolute():
            used_path = configured_path
        else:
            used_path = PROJECT_ROOT / configured_path
        used_path.mkdir(parents=True, exist_ok=True)
        return used_path
    except UndefinedValueError:
        return base_path_for_default / default_relative_to_base

# --- Path Definitions using the helper function ---

DATA_PATH: Path = _resolve_path(
    env_var_key="DATA_PATH",
    default_relative_to_base="data",
    base_path_for_default=PROJECT_ROOT,
)

INBOX_PATH: Path = _resolve_path(
    env_var_key="INBOX_PATH",
    default_relative_to_base="inbox",
    base_path_for_default=DATA_PATH,
)

FILE_PATH: Path = _resolve_path(
    env_var_key="FILE_PATH",
    default_relative_to_base="files",
    base_path_for_default=DATA_PATH,
)

MD_PATH: Path = _resolve_path(
    env_var_key="MD_PATH",
    default_relative_to_base="md",
    base_path_for_default=DATA_PATH,
)

DB_PATH: Path = _resolve_path(
    env_var_key="DB_PATH",  # Corrected key
    default_relative_to_base="db",
    base_path_for_default=DATA_PATH,
)

# --- DATABASE_URL Configuration ---
# Defaults to a sqlite file within the resolved DB_PATH.
DATABASE_URL_DEFAULT: str = f"sqlite:///{DB_PATH / 'jjobs.sqlite'}"
DATABASE_URL: str = config("DATABASE_URL", default=DATABASE_URL_DEFAULT)
