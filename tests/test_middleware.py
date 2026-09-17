"""Hook ordering: request hooks run in registration order, response hooks reversed."""

from __future__ import annotations

from typing import Any

from collector import Middleware


def _request_hook(tag: str, order: list[str]):
    async def hook(method: str, url: str, kwargs: dict[str, Any]) -> None:
        order.append(tag)

    return hook


def _response_hook(tag: str, order: list[str]):
    async def hook(response: Any, *, session: Any, retry: Any) -> Any:
        order.append(tag)
        return response

    return hook


def test_request_hooks_keep_registration_order():
    order: list[str] = []
    mw = Middleware()
    mw.request(_request_hook('first', order))
    mw.request(_request_hook('second', order))

    assert [h for h in mw.request_middleware] == list(mw.request_middleware)
    assert len(mw.request_middleware) == 2


def test_response_hooks_run_last_registered_first():
    order: list[str] = []
    mw = Middleware()
    outer = _response_hook('outer', order)
    inner = _response_hook('inner', order)
    mw.response(outer)
    mw.response(inner)

    assert list(mw.response_middleware) == [inner, outer]


def test_register_returns_the_hook_so_it_can_decorate():
    mw = Middleware()
    hook = _response_hook('x', [])
    assert mw.response(hook) is hook
