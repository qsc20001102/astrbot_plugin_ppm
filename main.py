"""Standalone PPM server."""
import os
from aiohttp import web
from ppm.server import create_app

if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=int(os.getenv("PPM_PORT", "8080")))
