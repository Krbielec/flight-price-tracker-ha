"""Response-returning read services for authenticated HA dashboard users."""

from __future__ import annotations

import asyncio
from datetime import date
import re
from uuid import uuid4

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

DOMAIN = "flight_price_tracker"
REQUEST_EVENT = "flight_price_tracker_read_requested"
RESPONSE_EVENT = "flight_price_tracker_read_response"
RESPONSE_TIMEOUT = 60
MAX_PENDING = 16


def _page_size(value):
    if type(value) is not int or not 1 <= value <= 500:
        raise vol.Invalid("page_size must be an integer between 1 and 500")
    return value


def _date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        date.fromisoformat(value)
        return value
    except ValueError:
        raise vol.Invalid("Expected YYYY-MM-DD") from None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    pending = {}

    def handle_response(event):
        data = event.data
        request_id = data.get("request_id")
        future = pending.get(request_id) if isinstance(request_id, str) else None
        if future is None or future.done():
            return
        if data.get("ok") is True and isinstance(data.get("result"), dict):
            future.set_result(data["result"])
        elif data.get("ok") is False and isinstance(data.get("error"), dict):
            # Only the safe error message produced by the add-on is exposed.
            code = data["error"].get("code", "read_failed")
            future.set_exception(HomeAssistantError(f"Flight Price Tracker read failed ({code})."))
        else:
            future.set_exception(HomeAssistantError("Flight Price Tracker returned an invalid response."))

    hass.bus.async_listen(RESPONSE_EVENT, handle_response)

    async def request_read(call: ServiceCall, operation: str, parameters: dict):
        user_id = call.context.user_id
        user = await hass.auth.async_get_user(user_id) if user_id else None
        if user is None or user.is_active is not True:
            raise ServiceValidationError("An authenticated active HA user is required.")
        # Non-admins are intentionally allowed; this component registers no writes.
        if len(pending) >= MAX_PENDING:
            raise HomeAssistantError("Flight Price Tracker is busy. Try again later.")
        request_id = uuid4().hex
        future = hass.loop.create_future()
        pending[request_id] = future
        try:
            hass.bus.async_fire(REQUEST_EVENT,
                {"request_id": request_id, "operation": operation, "parameters": parameters},
                context=call.context)
            return await asyncio.wait_for(future, timeout=RESPONSE_TIMEOUT)
        except TimeoutError:
            raise HomeAssistantError("Flight Price Tracker add-on did not respond.") from None
        finally:
            pending.pop(request_id, None)

    async def get_checks(call):
        return await request_read(call, "get_checks", dict(call.data))

    async def get_observations(call):
        return await request_read(call, "get_observations", dict(call.data))

    hass.services.async_register(DOMAIN, "get_checks", get_checks,
        schema=vol.Schema({vol.Optional("include_disabled", default=True): bool}),
        supports_response=SupportsResponse.ONLY)
    hass.services.async_register(DOMAIN, "get_observations", get_observations,
        schema=vol.Schema({
            vol.Required("check_id"): vol.All(str, vol.Match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")),
            vol.Optional("page_size", default=200): _page_size,
            vol.Optional("cursor"): vol.All(str, vol.Length(min=1, max=4096)),
            vol.Optional("checked_from"): _date,
            vol.Optional("checked_to"): _date,
        }), supports_response=SupportsResponse.ONLY)
    return True
