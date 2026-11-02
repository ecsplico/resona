"""Endpoint ``unload_url``: only loopback, only on local_llm endpoints."""

import pytest
from pydantic import ValidationError

from resona_job_core import Endpoint, OrganizationSettings

BASE = {"id": "gpu-llama", "kind": "local_llm", "model": "openai/qwen3.6-27b", "url": "http://127.0.0.1:9292/v1"}


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:9292/api/models/unload/qwen3.6-27b",
    "http://localhost:9292/api/models/unload",
    "http://[::1]:9292/api/models/unload",
])
def test_loopback_unload_url_is_accepted(url: str) -> None:
    assert Endpoint.model_validate({**BASE, "unload_url": url}).unload_url == url


@pytest.mark.parametrize("url", [
    "http://192.168.1.20:9292/api/models/unload",
    "https://llm.example.com/api/models/unload",
    "http://10.0.0.1/unload",
    "file:///127.0.0.1/unload",
    "127.0.0.1:9292/unload",
])
def test_non_loopback_unload_url_is_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate({**BASE, "unload_url": url})


def test_unload_url_is_rejected_on_cloud_endpoints() -> None:
    with pytest.raises(ValidationError):
        Endpoint.model_validate({**BASE, "kind": "cloud_llm", "unload_url": "http://127.0.0.1:9292/unload"})


def test_settings_with_a_non_loopback_unload_url_do_not_validate() -> None:
    with pytest.raises(ValidationError):
        OrganizationSettings.model_validate(
            {"endpoints": [{**BASE, "unload_url": "http://gpu-box.lan:9292/api/models/unload"}]}
        )
