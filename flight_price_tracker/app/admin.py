"""Administrator-only Ingress interface for checks."""

import logging
from pathlib import Path

from aiohttp import web

from .authorization import AuthorizationError, HAAdminAuthorizer
from .contracts import decode_json, encode_json
from .s3_checks import ChecksReadError, S3Checks, valid_etag

LOG = logging.getLogger(__name__)
INGRESS_PROXY_IP = "172.30.32.2"
ADMIN_HTML = Path(__file__).resolve().parent.parent / "www" / "admin.html"


@web.middleware
async def ingress_only(request: web.Request, handler):
    # Check the TCP peer, never client-supplied forwarding/role headers.
    if request.remote != INGRESS_PROXY_IP:
        raise web.HTTPForbidden(text="Use Home Assistant Ingress to access this page.")
    return await handler(request)


def create_app(store: S3Checks, authorizer: HAAdminAuthorizer | None = None) -> web.Application:
    authorizer = authorizer if authorizer is not None else HAAdminAuthorizer()

    @web.middleware
    async def administrator_only(request, handler):
        try:
            await authorizer.require_admin(request.headers.get("X-Remote-User-Id"))
            return await handler(request)
        except (AuthorizationError, ChecksReadError) as exc:
            LOG.warning("Admin request failed (%s)", exc.code)
            return web.json_response(
                {"error": {"code": exc.code, "message": str(exc)}}, status=exc.status,
                headers={"Cache-Control": "no-store"},
            )

    app = web.Application(middlewares=[ingress_only, administrator_only], client_max_size=16 * 1024)

    async def index(_request):
        return web.FileResponse(ADMIN_HTML, headers={"Cache-Control": "no-store"})

    async def airports(_request):
        return web.FileResponse(ADMIN_HTML.parent / "airports.json", headers={"Cache-Control": "no-store"})

    async def helpers(_request):
        return web.FileResponse(ADMIN_HTML.parent / "admin-helpers.mjs", headers={"Cache-Control": "no-store"})

    async def checks(_request):
        payload, etag = await store.snapshot()
        return web.json_response(payload, dumps=encode_json, headers={"Cache-Control": "no-store", "ETag": etag})

    async def mutation_body(request):
        # Requiring JSON and a non-simple If-Match header prevents cross-origin
        # form submissions. No CORS support is exposed by this application.
        if request.content_type != "application/json":
            raise ChecksReadError("invalid_request", "Expected application/json.", 400)
        etag = request.headers.get("If-Match")
        if not valid_etag(etag) or len(request.headers.getall("If-Match", [])) != 1:
            raise ChecksReadError("invalid_request", "A single strong If-Match ETag is required.", 400)
        try:
            body = await request.json(loads=decode_json)
        except (ValueError, UnicodeError, RecursionError):
            raise ChecksReadError("invalid_request", "Expected a valid JSON object.", 400) from None
        return body, etag

    async def create_check(request):
        body, etag = await mutation_body(request)
        check, new_etag = await store.create(body, etag)
        return web.json_response(check, status=201, dumps=encode_json,
                                 headers={"Cache-Control": "no-store", "ETag": new_etag})

    async def set_enabled(request):
        body, etag = await mutation_body(request)
        if not isinstance(body, dict) or set(body) != {"enabled"} or type(body["enabled"]) is not bool:
            raise ChecksReadError("invalid_request", "Expected only a boolean enabled field.", 400)
        check, new_etag = await store.set_enabled(request.match_info["check_id"], body["enabled"], etag)
        return web.json_response(check, dumps=encode_json, headers={"Cache-Control": "no-store", "ETag": new_etag})

    async def cleanup(_app):
        await store.close()

    app.router.add_get("/", index)
    app.router.add_get("/airports.json", airports)
    app.router.add_get("/admin-helpers.mjs", helpers)
    app.router.add_get("/api/checks", checks)
    app.router.add_post("/api/checks", create_check)
    app.router.add_patch("/api/checks/{check_id}/enabled", set_enabled)
    app.on_cleanup.append(cleanup)
    return app
