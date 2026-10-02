import base64
import hmac
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web
from .llm import ModelClient
from .settings import Settings
from .storage import prepare_data
from .web_api import WebApi

WEB_ROOT = Path(__file__).resolve().parents[1] / "pages" / "ppm"

def create_app(data_dir=None, password=None):
    data_dir = Path(data_dir or os.getenv("PPM_DATA_DIR", "data")).resolve()
    password = os.getenv("PPM_PASSWORD", "") if password is None else password

    @web.middleware
    async def access(request, handler):
        if password and request.path != "/healthz":
            expected = "Basic " + base64.b64encode(("admin:" + password).encode()).decode()
            if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
                return web.Response(status=401, headers={"WWW-Authenticate": 'Basic realm="PPM", charset="UTF-8"'}, text="请使用 admin 及部署密码登录")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return web.json_response({"error": "不允许跨站修改数据"}, status=403)
            if request.content_type != "application/json":
                return web.json_response({"error": "请求必须使用 application/json"}, status=415)
        try:
            response = await handler(request)
        except web.HTTPException:
            raise
        except Exception:
            logging.getLogger(__name__).exception("Request failed")
            return web.json_response({"error": "服务内部错误，请查看服务日志"}, status=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store" if request.path.startswith("/api/") else "no-cache"
        return response

    app = web.Application(middlewares=[access], client_max_size=1024 * 1024)
    database = prepare_data(data_dir)
    settings = Settings(data_dir / "settings.json")
    WebApi(database, settings, ModelClient(settings)).register(app)

    async def health(request):
        return web.json_response({"ok": True})

    async def index(request):
        return web.FileResponse(WEB_ROOT / "index.html")

    async def asset(request):
        name = request.match_info["name"]
        path = WEB_ROOT / name
        if path.suffix not in (".js", ".css", ".png", ".ico", ".svg") or not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    app.router.add_get("/healthz", health)
    app.router.add_get("/", index)
    app.router.add_get("/{name}", asset)
    return app
