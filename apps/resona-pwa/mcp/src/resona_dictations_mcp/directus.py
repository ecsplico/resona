"""The only module that talks to Directus.

Every call carries the calling user's own token. Which rows come back is decided by
Directus (the ``user`` policy and the owner-guard extension): queries here add no owner
filter, so a policy bug would show up in the isolation tests instead of being masked.
Nothing in this module logs: bodies hold transcript text and headers hold tokens.
"""
import logging
import re
import secrets
from collections.abc import AsyncIterator
from typing import Any, Optional

import httpx


# httpx logs every request URL at INFO. Queries go in bodies (see ``Directus.items``),
# but nothing about a user's requests belongs in this service's log beyond its own lines.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


class DirectusError(Exception):
    """Directus answered with an error status. Carries the status only, never a body."""

    def __init__(self, status: int):
        super().__init__(f"Directus answered {status}")
        self.status = status


class Unauthorized(DirectusError):
    """Directus does not accept the token (invalid, expired or revoked)."""

    def __init__(self) -> None:
        super().__init__(401)


class NotFound(DirectusError):
    """No such row for this user. Directus answers 403 both for a missing row and for
    one the policy hides, so the two are indistinguishable by design."""

    def __init__(self) -> None:
        super().__init__(404)


class DirectusUnavailable(Exception):
    """Directus could not be reached or failed (5xx): nothing is known about the token."""


def new_http_client(base_url: str) -> httpx.AsyncClient:
    """The shared connection pool. It carries no credential of its own."""
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0)


def _raise_for(status: int) -> None:
    if status == 401:
        raise Unauthorized()
    if status in (403, 404):
        raise NotFound()
    if status >= 500:
        raise DirectusUnavailable()
    raise DirectusError(status)


# What ``upload_file`` puts into a multipart header: no quotes, no line breaks, ASCII.
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9 ._()+-]{1,200}$")
_SAFE_TYPE = re.compile(r"^[a-z]+/[a-z0-9.+-]+$")


class Directus:
    """Directus REST as one user: ``http`` is the shared pool, ``token`` the caller's."""

    def __init__(self, http: httpx.AsyncClient, token: str):
        self._http = http
        self._auth = {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, *, headers: Optional[dict[str, str]] = None, **kwargs: Any) -> Any:
        try:
            resp = await self._http.request(method, path, headers={**self._auth, **(headers or {})}, **kwargs)
        except httpx.TransportError:
            raise DirectusUnavailable() from None
        if resp.status_code == 204:
            return None
        if resp.status_code != 200:
            _raise_for(resp.status_code)
        return resp.json()["data"]

    async def me(self) -> str:
        """The id of the user the token belongs to."""
        return str((await self._request("GET", "/users/me", params={"fields": "id"}))["id"])

    async def items(
        self,
        collection: str,
        *,
        fields: list[str],
        filter: Optional[dict[str, Any]] = None,
        sort: Optional[list[str]] = None,
        limit: int,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Rows of ``collection``. Sent as Directus' ``SEARCH`` method: the query (a
        search needle may be a name) travels in the body, never in a URL that the
        HTTP client, a proxy or Directus would write to an access log."""
        query: dict[str, Any] = {"fields": fields, "limit": limit}
        if offset:
            query["offset"] = offset
        if filter:
            query["filter"] = filter
        if sort:
            query["sort"] = sort
        return await self._request("SEARCH", f"/items/{collection}", json={"query": query})

    async def item(self, collection: str, item_id: str, *, fields: list[str]) -> dict[str, Any]:
        return await self._request("GET", f"/items/{collection}/{item_id}", params={"fields": ",".join(fields)})

    async def create(self, collection: str, data: dict[str, Any], *, fields: list[str]) -> dict[str, Any]:
        """Create one row; returns ``fields`` of it."""
        return await self._request("POST", f"/items/{collection}", params={"fields": ",".join(fields)}, json=data)

    async def update(self, collection: str, item_id: str, data: dict[str, Any], *, fields: list[str]) -> dict[str, Any]:
        """Patch one row; returns ``fields`` of the updated row."""
        return await self._request("PATCH", f"/items/{collection}/{item_id}", params={"fields": ",".join(fields)}, json=data)

    async def delete(self, collection: str, item_id: str) -> None:
        """Delete one row (Directus answers 204)."""
        await self._request("DELETE", f"/items/{collection}/{item_id}")

    async def upload_file(
        self, body: AsyncIterator[bytes], *, length: int, filename: str, content_type: str,
    ) -> str:
        """Stream ``length`` bytes from ``body`` into Directus files as this user; the new
        file id. ``content_type`` becomes the file's type (the asset Content-Type)."""
        if not _SAFE_FILENAME.match(filename) or not _SAFE_TYPE.match(content_type):
            raise ValueError("unsafe filename or content type")
        boundary = secrets.token_hex(16)
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n").encode()
        tail = f"\r\n--{boundary}--\r\n".encode()

        async def multipart() -> AsyncIterator[bytes]:
            yield head
            async for chunk in body:
                yield chunk
            yield tail

        data = await self._request(
            "POST", "/files", content=multipart(), params={"fields": "id"},
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     "Content-Length": str(len(head) + length + len(tail))},
        )
        return str(data["id"])

    async def open_asset(self, file_id: str, *, byte_range: Optional[str] = None) -> httpx.Response:
        """The file's bytes as this user: an open streaming response (200 or 206) that the
        caller must ``aclose``. ``byte_range`` is passed on as the ``Range`` header."""
        headers = {**self._auth, "Accept-Encoding": "identity"}
        if byte_range:
            headers["Range"] = byte_range
        request = self._http.build_request("GET", f"/assets/{file_id}", headers=headers)
        try:
            resp = await self._http.send(request, stream=True)
        except httpx.TransportError:
            raise DirectusUnavailable() from None
        if resp.status_code in (200, 206):
            return resp
        await resp.aclose()
        _raise_for(resp.status_code)
        raise AssertionError("unreachable")
