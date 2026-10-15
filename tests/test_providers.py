"""LLM providers: the private-network guard for visitor-supplied addresses, endpoints and key checks (no network)."""
from __future__ import annotations

import asyncio
import socket
from unittest import mock

import httpx
import pytest

from operaitor.agents import providers as P
from operaitor.agents.providers import Endpoint, check_endpoint, check_public_url, endpoint_for


def _resolves_to(*ips):
    return lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]


@pytest.mark.parametrize("url, why", [
    ("http://ollama.example.org", "https"),
    ("https://", "not a valid URL"),
])
def test_public_url_needs_https_and_a_host(url, why):
    assert why in check_public_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.0.10", "172.16.5.4", "169.254.169.254", "::1", "0.0.0.0"])
def test_public_url_refuses_private_and_local_addresses(ip):
    with mock.patch("socket.getaddrinfo", _resolves_to(ip)):
        assert "private network" in check_public_url("https://looks-public.example.org")


def test_public_url_accepts_a_public_address_and_reports_dns_failures():
    with mock.patch("socket.getaddrinfo", _resolves_to("93.184.216.34")):
        assert check_public_url("https://ollama.example.org:8443") is None
    with mock.patch("socket.getaddrinfo", _resolves_to("93.184.216.34", "10.0.0.1")):   # any private answer refuses
        assert "private" in check_public_url("https://ollama.example.org")
    with mock.patch("socket.getaddrinfo", side_effect=OSError("nxdomain")):
        assert "cannot resolve" in check_public_url("https://nope.invalid")


def test_endpoints_and_public_view_hide_the_key():
    ep = endpoint_for("groq", "llama-3.3-70b-versatile", "gsk-secret")
    assert ep.base_url == P.PROVIDERS["groq"]["base_url"] and ep.json_mode is True
    assert "gsk-secret" not in str(ep.public()) and ep.public()["in_browser"] is False
    oll = endpoint_for("ollama", "qwen3:8b", "", "https://my.example.org/v1/")
    assert oll.base_url == "https://my.example.org/v1" and oll.api_key == "ollama"
    assert oll.public()["url"] == "https://my.example.org/v1"
    assert endpoint_for("ollama", "llama3.2").base_url.endswith(":11434/v1")
    assert Endpoint("groq", "b", "", "m", True, relay=object()).in_browser


def _mock_http(handler):
    real = httpx.AsyncClient
    return mock.patch("httpx.AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_check_endpoint_outcomes_without_network():
    run = asyncio.run
    assert run(check_endpoint(endpoint_for("groq", "m"), public=True)) == (False, P.PROVIDERS["groq"]["label"]
                                                                            + f" needs an API key ({P.PROVIDERS['groq']['signup']})")
    with _mock_http(lambda req: httpx.Response(200, json={"data": []})):
        assert run(check_endpoint(endpoint_for("openai", "gpt-5-nano", "sk-x"), public=False)) == (True, "connected")
    with _mock_http(lambda req: httpx.Response(401)):
        ok, why = run(check_endpoint(endpoint_for("groq", "m", "bad"), public=False))
        assert not ok and "rejected this key (401)" in why
    with _mock_http(lambda req: httpx.Response(503)):
        assert "answered 503" in run(check_endpoint(endpoint_for("gemini", "m", "k"), public=False))[1]
    with _mock_http(lambda req: httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})):
        assert run(check_endpoint(endpoint_for("ollama", "llama3.2"), public=False))[0] is True
        ok, why = run(check_endpoint(endpoint_for("ollama", "qwen3:8b"), public=False))
        assert not ok and "no model 'qwen3:8b'" in why

    def boom(req):
        raise httpx.ConnectError("down")
    with _mock_http(boom):
        assert "could not reach" in run(check_endpoint(endpoint_for("openrouter", "m", "k"), public=False))[1]
    with mock.patch("socket.getaddrinfo", _resolves_to("10.0.0.7")):   # public server: never connects inside
        assert "private network" in run(check_endpoint(endpoint_for("ollama", "m", "", "https://x.example"), public=True))[1]
