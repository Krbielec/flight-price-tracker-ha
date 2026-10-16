"""Read-only HTTP interface, reachable exclusively through HA Ingress."""

import logging
from pathlib import Path

from aiohttp import web

from .s3_checks import ChecksReadError, S3Checks

LOG = logging.getLogger(__name__)
INGRESS_PROXY_IP = "172.30.32.2"
ADMIN_HTML = Path(__file__).resolve().parent.parent / "www" / "admin.html"


@web.middleware
async def ingress_only(request: web.Request, handler):
    # This checks the actual TCP peer, never a client-supplied forwarded header.
    # It identifies Supervisor's proxy, NOT an administrator role. There are no
    # mutations here; future writes require independent server-side admin checks.
    if request.remote != INGRESS_PROXY_IP:
        raise web.HTTPForbidden(text="Use Home Assistant Ingress to access this page.")
    return await handler(request)


def create_app(store: S3Checks) -> web.Application:
    app = web.Application(middlewares=[ingress_only], client_max_size=16 * 1024)

    async def index(_request):
        return web.FileResponse(ADMIN_HTML, headers={"Cache-Control": "no-store"})

    async def checks(_request):
        try:
            payload = await store.read()
        except ChecksReadError as exc:
            LOG.warning("Check configuration read failed (%s)", exc.code)
            return web.json_response(
                {"error": {"code": exc.code, "message": str(exc)}},
                status=exc.status,
                headers={"Cache-Control": "no-store"},
            )
        return web.json_response(payload, headers={"Cache-Control": "no-store"})

    async def cleanup(_app):
        await store.close()

    app.router.add_get("/", index)
    app.router.add_get("/api/checks", checks)
    app.on_cleanup.append(cleanup)
    return app
