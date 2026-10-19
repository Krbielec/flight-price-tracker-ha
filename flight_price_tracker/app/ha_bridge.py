"""Read-only HA service/event bridge. It has no mutation dispatch path."""

import asyncio
import logging
import os
import re

from aiohttp import ClientError, ClientSession, ClientTimeout, WSMsgType

from .contracts import encode_json
from .s3_checks import ChecksReadError

REQUEST_EVENT = "flight_price_tracker_read_requested"
RESPONSE_EVENT = "flight_price_tracker_read_response"
WEBSOCKET_URL = "http://supervisor/core/websocket"
EVENTS_URL = "http://supervisor/core/api/events/"
LOG = logging.getLogger(__name__)


class HomeAssistantBridge:
    def __init__(self, reader, token=None):
        self._reader = reader
        self._token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN")

    async def dispatch(self, event):
        data = event.get("data") if isinstance(event, dict) else None
        context = event.get("context") if isinstance(event, dict) else None
        if (not isinstance(data, dict) or not isinstance(context, dict)
                or not isinstance(context.get("user_id"), str) or not context["user_id"]):
            return None
        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            return None
        try:
            if set(data) != {"request_id", "operation", "parameters"} or not isinstance(data["parameters"], dict):
                raise ChecksReadError("invalid_parameters", "Invalid read request.", 400)
            operation, params = data["operation"], data["parameters"]
            if operation == "get_checks" and set(params) <= {"include_disabled"}:
                result = await self._reader.get_checks(**params)
            elif operation == "get_observations" and "check_id" in params and set(params) <= {"check_id", "page_size", "cursor", "checked_from", "checked_to"}:
                result = await self._reader.get_observations(**params)
            else:
                raise ChecksReadError("invalid_operation", "Only supported read operations are available.", 400)
            return {"request_id": request_id, "ok": True, "result": result}
        except ChecksReadError as exc:
            return {"request_id": request_id, "ok": False,
                    "error": {"code": exc.code, "message": str(exc), "status": exc.status}}

    async def _respond(self, session, response):
        if response is None:
            return
        async with session.post(EVENTS_URL + RESPONSE_EVENT, json=response,
                                headers={"Authorization": f"Bearer {self._token}"}) as reply:
            reply.raise_for_status()

    async def _handle(self, session, event):
        try:
            await self._respond(session, await self.dispatch(event))
        except (ClientError, TimeoutError, ValueError, TypeError):
            LOG.warning("Could not return an HA read response")
        except Exception:
            # Do not log tracebacks or raw payloads from the HA transport.
            LOG.error("Unexpected read bridge failure")

    async def run(self):
        if not self._token:
            LOG.warning("HA read bridge is unavailable: Supervisor token is missing")
            return
        delay = 5
        while True:
            tasks = set()
            try:
                async with ClientSession(timeout=ClientTimeout(total=15), json_serialize=encode_json,
                                         trust_env=False) as session:
                    async with session.ws_connect(WEBSOCKET_URL, heartbeat=30, max_msg_size=512 * 1024) as ws:
                        async with asyncio.timeout(10):
                            if (await ws.receive_json()).get("type") != "auth_required":
                                raise ValueError
                            await ws.send_json({"type": "auth", "access_token": self._token})
                            if (await ws.receive_json()).get("type") != "auth_ok":
                                raise ValueError
                            await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": REQUEST_EVENT})
                            reply = await ws.receive_json()
                            if reply.get("id") != 1 or reply.get("success") is not True:
                                raise ValueError
                        delay = 5
                        LOG.info("HA read bridge connected")
                        try:
                            async for message in ws:
                                if message.type != WSMsgType.TEXT:
                                    break
                                payload = message.json()
                                if payload.get("type") != "event":
                                    continue
                                event = payload.get("event", {})
                                if not isinstance(event, dict) or event.get("event_type") != REQUEST_EVENT:
                                    continue
                                if len(tasks) >= 4:
                                    # Bounded work: no unbounded task/SDK queue.
                                    data = event.get("data", {})
                                    request_id = data.get("request_id") if isinstance(data, dict) else None
                                    if isinstance(request_id, str) and re.fullmatch(r"[a-f0-9]{32}", request_id):
                                        await self._respond(session, {"request_id": request_id, "ok": False,
                                            "error": {"code": "reader_busy", "message": "Read bridge is busy. Try again later.", "status": 503}})
                                    continue
                                task = asyncio.create_task(self._handle(session, event))
                                tasks.add(task)
                                task.add_done_callback(tasks.discard)
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                        raise ConnectionError
            except (ClientError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
                LOG.warning("HA read bridge disconnected; retrying")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
