import re
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .ppm import Database
from .ppm.errors import NotFoundError, ValidationError
from .ppm.web_api import WebApi

PLUGIN_NAME = "astrbot_plugin_ppm"
PROJECT_OVERVIEW_COMMAND = "项目总览"
PROJECT_PROGRESS_COMMAND = "项目进程"
PROJECT_SUMMARY_COMMAND = "每日总结"
PROJECT_LIFECYCLE_COMMAND = "项目总结"


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


def _project_overview_args(message: str) -> list[str]:
    """Extract status arguments from the normalized AstrBot command message."""
    return _command_body(message, PROJECT_OVERVIEW_COMMAND).split()


def _parse_project_process_args(message: str) -> tuple[str, str]:
    """Parse one project ID and preserve the remainder as free-form content."""
    body = _command_body(message, PROJECT_PROGRESS_COMMAND)
    match = re.match(r"^(\S+)(?:\s+(.*))?$", body, re.DOTALL)
    if not match or not match.group(2) or not match.group(2).strip():
        raise ValidationError(
            "项目进程参数错误：需要项目ID和记录文本。\n"
            "正确写法：/项目进程 <项目ID> <记录文本>"
        )
    return match.group(1), match.group(2)


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


def _format_project_overview(
    projects: list[dict[str, object]], statuses: list[str]
) -> str:
    selected = "、".join(statuses) if statuses else "除结束外（默认）"
    lines = ["项目总览", f"筛选状态：{selected}"]
    if not projects:
        lines.append("暂无符合条件的项目。")
        return "\n".join(lines)
    lines.append("项目ID | 项目名称 | 状态")
    lines.extend(
        f"{project['id']} | {project['name']} | {project['status']}"
        for project in projects
    )
    return "\n".join(lines)


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

    @filter.command(PROJECT_PROGRESS_COMMAND)
    async def project_process(self, event: AstrMessageEvent):
        """Add today's free-form work record to one project."""
        try:
            project_id, content = _parse_project_process_args(event.message_str)
            work_date = _today_iso()
            self.database.save_work_log(
                {
                    "project_id": project_id,
                    "work_date": work_date,
                    "content": content,
                }
            )
            reply = f"{work_date}，记录添加成功"
        except (ValidationError, NotFoundError) as exc:
            reply = f"项目进程记录添加失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - command handler must return a user-facing error
            logger.exception("项目进程指令执行失败")
            reply = f"项目进程记录添加失败：{exc}"
        yield event.plain_result(reply)
        event.stop_event()

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

    @filter.command(PROJECT_LIFECYCLE_COMMAND)
    async def project_lifecycle_summary(self, event: AstrMessageEvent):
        """Summarize all recorded stages of one project."""
        try:
            args = _command_body(event.message_str, PROJECT_LIFECYCLE_COMMAND).split()
            if len(args) != 1 or not re.fullmatch(r"[0-9]+", args[0]):
                raise ValidationError("正确写法：/项目总结 <项目ID>，仅接受一个纯数字项目ID")
            reply = await self.web_api.generate_project_summary(args[0])
        except (ValidationError, NotFoundError) as exc:
            reply = f"项目总结生成失败：{exc}"
        except Exception as exc:  # noqa: BLE001 - command errors must be visible
            logger.exception("项目总结指令执行失败")
            reply = f"项目总结生成失败：{exc}"
        yield event.plain_result(reply)
        event.stop_event()

    @filter.command(PROJECT_OVERVIEW_COMMAND)
    async def project_overview(self, event: AstrMessageEvent):
        """在消息平台中查询项目 ID、名称和状态；不填写状态时默认排除结束项目。"""
        statuses = _project_overview_args(event.message_str)
        try:
            projects = self.database.list_project_overview(statuses)
            reply = _format_project_overview(projects, statuses)
        except ValidationError as exc:
            reply = str(exc)
        except Exception:  # noqa: BLE001 - command handler must return a user-facing error
            logger.exception("项目总览指令执行失败")
            reply = "项目总览查询失败，请稍后再试。"
        yield event.plain_result(reply)
        event.stop_event()

    async def terminate(self):
        """No persistent SQLite connection is held, so shutdown is immediate."""
        logger.info("PPM plugin terminated")
