from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .ppm import Database
from .ppm.errors import NotFoundError, ValidationError
from .ppm.web_api import WebApi
from .ppm.todo_service import TodoReminderService

PLUGIN_NAME = "astrbot_plugin_ppm"
PROJECT_SUMMARY_COMMAND = "每日总结"


def _command_body(message: str, command: str) -> str:
    """Remove a command root while leaving its payload text untouched."""
    text = str(message or "").lstrip()
    text = text.removeprefix("/")
    if text == command:
        return ""
    remainder = text[len(command) :]
    if text.startswith(command) and (not remainder or remainder[0].isspace()):
        return remainder.lstrip()
    return text


def _parse_project_summary_args(message: str) -> tuple[int, list[str]]:
    """Parse history days followed by optional project IDs."""
    args = _command_body(message, PROJECT_SUMMARY_COMMAND).split()
    if not args:
        return 1, []
    try:
        history_days = int(args[0])
    except ValueError as exc:
        raise ValidationError(
            "携带的总结天数必须是整数。\n"
            "正确写法：/每日总结 <天数> [项目ID] [项目ID] ..."
        ) from exc
    if history_days < 0 or history_days > 30:
        raise ValidationError("携带的总结天数必须在 0 到 30 之间")
    return history_days, args[1:]


def _today_iso() -> str:
    return datetime.now().astimezone().date().isoformat()


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
        self.todo_reminders = TodoReminderService(
            self.database, config, self._send_todo_reminder, logger
        )
        logger.info("PPM plugin initialized; database=%s", data_dir / "ppm.sqlite3")

    async def initialize(self):
        self.todo_reminders.start()

    async def _send_todo_reminder(self, session: str, text: str) -> bool:
        return await self.context.send_message(session, MessageChain().message(text))

    @filter.command(PROJECT_SUMMARY_COMMAND)
    async def project_summary(self, event: AstrMessageEvent):
        """Generate and overwrite today's project summary."""
        try:
            history_days, requested_ids = _parse_project_summary_args(
                event.message_str
            )
            summary_date = _today_iso()
            project_ids = (
                requested_ids or self.database.list_default_summary_project_ids(summary_date)
            )
            if not project_ids:
                raise ValidationError("没有可生成日报的项目")
            result = await self.web_api.generate_summary(
                project_ids,
                summary_date,
                history_days,
                overwrite=True,
            )
            reply = result["content"]
        except (ValidationError, NotFoundError) as exc:
            reply = f"每日总结生成失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - command handler must return a user-facing error
            logger.exception("每日总结指令执行失败")
            reply = f"每日总结生成失败：{exc}"
        yield event.plain_result(reply)
        event.stop_event()

    async def terminate(self):
        await self.todo_reminders.stop()
        logger.info("PPM plugin terminated")
