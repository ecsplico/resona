"""Local MCP over stdio: one hub, one organization key from the environment.

``RESONA_API_URL`` / ``RESONA_API_KEY`` select the hub and credential. ``path`` input is
enabled only when ``RESONA_MCP_ROOT`` names an existing directory; there is no default
root. Logs go to stderr; stdout carries only the protocol.
"""
from pathlib import Path
from typing import Optional

from decouple import config
from mcp.server.mcpserver import MCPServer
from resona_client.client import ResonaClient

from .server import InputPolicy, build_server


class StdioConfigError(RuntimeError):
    pass


def create_stdio_server(
    *,
    hub_url: Optional[str] = None,
    api_key: Optional[str] = None,
    root: Optional[str] = None,
) -> MCPServer:
    url = hub_url or config("RESONA_API_URL", default="")
    key = api_key or config("RESONA_API_KEY", default="")
    if not url or not key:
        raise StdioConfigError("RESONA_API_URL and RESONA_API_KEY must be set")
    root_value = root if root is not None else config("RESONA_MCP_ROOT", default="")
    root_path: Optional[Path] = None
    if root_value:
        root_path = Path(root_value)
        if not root_path.is_dir():
            raise StdioConfigError("RESONA_MCP_ROOT must name an existing directory")
        root_path = root_path.resolve()
    return build_server(
        lambda ctx: ResonaClient(base_url=url, api_key=key, timeout=120.0),
        InputPolicy(allow_paths=True, root=root_path),
    )
