"""Resolve the trusted Ingress identity against live Home Assistant roles."""

import asyncio
import os

from aiohttp import ClientError, ClientSession, ClientTimeout

HA_WEBSOCKET_URL = "http://supervisor/core/websocket"


class AuthorizationError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


class HAAdminAuthorizer:
    def __init__(self, token: str | None = None):
        self._token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN")

    async def require_admin(self, user_id: str | None) -> None:
        # Supervisor strips caller-supplied identity headers. Trust this ID only
        # AFTER checking the actual TCP peer in ingress_only.
        if not user_id or len(user_id) > 128:
            raise AuthorizationError(403, "admin_required", "An identified HA administrator is required.")
        if not self._token:
            raise AuthorizationError(503, "authorization_unavailable", "HA authorization is unavailable.")
        try:
            async with asyncio.timeout(10):
                async with ClientSession(timeout=ClientTimeout(total=10), trust_env=False) as session:
                    async with session.ws_connect(HA_WEBSOCKET_URL, max_msg_size=1024 * 1024) as ws:
                        if (await ws.receive_json()).get("type") != "auth_required":
                            raise ValueError
                        await ws.send_json({"type": "auth", "access_token": self._token})
                        if (await ws.receive_json()).get("type") != "auth_ok":
                            raise ValueError
                        await ws.send_json({"id": 1, "type": "config/auth/list"})
                        reply = await ws.receive_json()
                        if reply.get("id") != 1 or reply.get("success") is not True:
                            raise ValueError
                        users = reply.get("result")
                        if not isinstance(users, list) or any(not isinstance(user, dict) for user in users):
                            raise ValueError
        except (ClientError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
            # Never include HA replies, tokens, names or raw exception text.
            raise AuthorizationError(503, "authorization_unavailable", "HA authorization is unavailable.") from None
        user = next((user for user in users if user.get("id") == user_id), {})
        groups = user.get("group_ids")
        if user.get("is_active") is not True or not (
            user.get("is_owner") is True or isinstance(groups, list) and "system-admin" in groups
        ):
            raise AuthorizationError(403, "admin_required", "An active HA administrator is required.")
