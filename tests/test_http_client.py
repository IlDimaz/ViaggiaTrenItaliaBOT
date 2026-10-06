"""Guardrails for the shared Trenitalia HTTP client configuration."""
from __future__ import annotations

import config
import trenitalia.lefrecce as lf
import trenitalia.viaggiatreno as vt


async def test_headers_are_browser_like():
    assert "Mozilla" in config.HTTP_HEADERS["User-Agent"]
    assert config.HTTP_HEADERS["Accept"]
    assert config.HTTP_HEADERS["Accept-Language"]


async def test_viaggiatreno_client_config():
    assert vt.BASE.startswith("https://")
    assert vt.BASE_MOBILE.startswith("https://")
    c = vt.client()
    assert "Mozilla" in c.headers.get("user-agent", "")
    assert c.follow_redirects is True
    await vt.close()


async def test_lefrecce_client_config():
    c = lf.client()
    assert "Mozilla" in c.headers.get("user-agent", "")
    assert c.headers.get("content-type") == "application/json"
    assert c.follow_redirects is True
    await lf.close()