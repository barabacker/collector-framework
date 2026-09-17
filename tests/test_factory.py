"""build_http_client turns a parser's Settings into a configured client."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from collector import BaseParser, RetryPolicy, Settings, Throttle, build_http_client


class _Bare(BaseParser):
    name = '_bare'

    async def parse(self, response: Any):  # pragma: no cover - never run
        yield {}


async def _hook(response: Any, *, session: Any, retry: Any) -> Any:  # pragma: no cover
    return response


async def _req_hook(method: str, url: str, kwargs: dict[str, Any]) -> None:  # pragma: no cover
    return None


@pytest.fixture
def captured(monkeypatch):
    """Capture AsyncSession kwargs and the resolved extra-cert path."""
    seen: dict[str, Any] = {}

    class _FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            seen['session_kwargs'] = kwargs

        async def close(self) -> None:  # pragma: no cover
            pass

    def _fake_bundle(path: str) -> str:
        seen['cert_path'] = path
        return '/tmp/fake-bundle.pem'

    monkeypatch.setattr('collector.http.factory.AsyncSession', _FakeSession)
    monkeypatch.setattr('collector.http.factory.ca_bundle_with_extra_cert', _fake_bundle)
    return seen


def _with(**kwargs: Any) -> type[BaseParser]:
    # __module__ is what build_http_client resolves a relative cert path against.
    return type('_Configured', (_Bare,), {'settings': Settings(**kwargs), '__module__': __name__})


def test_defaults_impersonate_a_browser_and_set_a_timeout(captured):
    build_http_client(_Bare)
    assert captured['session_kwargs'] == {'impersonate': 'chrome', 'timeout': 30.0}


def test_transport_settings_reach_the_session(captured):
    build_http_client(
        _with(
            impersonate='safari',
            timeout=5.0,
            proxy='http://127.0.0.1:8080',
            headers={'Accept-Language': 'ru'},
        )
    )
    assert captured['session_kwargs'] == {
        'impersonate': 'safari',
        'timeout': 5.0,
        'proxy': 'http://127.0.0.1:8080',
        'headers': {'Accept-Language': 'ru'},
    }


def test_impersonate_none_is_omitted_rather_than_passed(captured):
    build_http_client(_with(impersonate=None, timeout=None))
    assert captured['session_kwargs'] == {}


def test_session_kwargs_is_the_escape_hatch_and_wins(captured):
    build_http_client(_with(timeout=5.0, session_kwargs={'timeout': 99.0, 'http_version': 2}))
    assert captured['session_kwargs']['timeout'] == 99.0
    assert captured['session_kwargs']['http_version'] == 2


def test_logging_hooks_are_always_registered(captured):
    client = build_http_client(_Bare)
    assert len(client.middleware.request_middleware) == 1
    assert len(client.middleware.response_middleware) == 1


def test_declared_response_hooks_run_before_the_logging_hook(captured):
    client = build_http_client(_with(response_hooks=(_hook,)))
    assert list(client.middleware.response_middleware)[0] is _hook


def test_declared_request_hooks_are_registered(captured):
    client = build_http_client(_with(request_hooks=(_req_hook,)))
    assert _req_hook in client.middleware.request_middleware


def test_delay_installs_a_throttle(captured):
    client = build_http_client(_with(delay=0.5, delay_jitter=0.2))
    throttles = [h for h in client.middleware.request_middleware if isinstance(h, Throttle)]
    assert len(throttles) == 1
    assert (throttles[0].delay, throttles[0].jitter) == (0.5, 0.2)


def test_no_delay_means_no_throttle(captured):
    client = build_http_client(_Bare)
    assert not any(isinstance(h, Throttle) for h in client.middleware.request_middleware)


def test_retry_policy_is_handed_to_the_client(captured):
    policy = RetryPolicy(attempts=7)
    client = build_http_client(_with(retry=policy))
    assert client.retry_policy is policy


def test_skip_tls_verify_disables_verification(captured):
    build_http_client(_with(skip_tls_verify=True))
    assert captured['session_kwargs']['verify'] is False


def test_extra_ca_cert_is_resolved_against_the_parser_module(captured):
    build_http_client(_with(extra_ca_cert='certs/site.pem'))
    assert Path(captured['cert_path']) == Path(__file__).parent / 'certs' / 'site.pem'
    assert captured['session_kwargs']['verify'] == '/tmp/fake-bundle.pem'


def test_a_subclass_narrows_its_parents_settings(captured):
    class _Parent(_Bare):
        settings = Settings(timeout=5.0, concurrency=4)

    class _Child(_Parent):
        settings = replace(_Parent.settings, timeout=1.0)

    build_http_client(_Child)
    assert captured['session_kwargs']['timeout'] == 1.0
    assert _Child.settings.concurrency == 4
