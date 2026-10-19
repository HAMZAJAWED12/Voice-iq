"""A route class whose JSON bodies must hold only finite numbers.

Python's `json.loads` accepts `NaN`, `Infinity` and `-Infinity`, and turns a
number such as `1e999` into infinity. Such a value cannot be rendered in a
strict JSON response — not even in the 422 that would reject it, because
the error echoes the input. Routes built with `FiniteJSONRoute` refuse
these bodies while parsing, with the standard 422 `json_invalid` error.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute


def _reject_constant(token: str) -> Any:
    raise json.JSONDecodeError(f"{token} is not a JSON number", token, 0)


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise json.JSONDecodeError("Number is too large", text, 0)
    return value


class _FiniteJSONRequest(Request):
    async def json(self) -> Any:
        if not hasattr(self, "_json"):
            body = await self.body()
            self._json = json.loads(body, parse_constant=_reject_constant, parse_float=_finite_float)
        return self._json


class FiniteJSONRoute(APIRoute):
    """An APIRoute that parses JSON bodies with finite numbers only."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def finite_json_handler(request: Request) -> Response:
            return await handler(_FiniteJSONRequest(request.scope, request.receive))

        return finite_json_handler


__all__ = ["FiniteJSONRoute"]
