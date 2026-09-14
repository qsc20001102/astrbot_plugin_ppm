from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .ppm import Database
from .ppm.web_api import WebApi

PLUGIN_NAME = "astrbot_plugin_ppm"


class PPMPlugin(Star):
    """Project Progress Management plugin entrypoint."""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        data_dir = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        self.database = Database(data_dir / "ppm.sqlite3")
        self.database.initialize()
        self.web_api = WebApi(self.database, context, config)
        self.web_api.register(PLUGIN_NAME)
        logger.info("PPM plugin initialized; database=%s", data_dir / "ppm.sqlite3")

    async def terminate(self):
        """No persistent SQLite connection is held, so shutdown is immediate."""
        logger.info("PPM plugin terminated")
