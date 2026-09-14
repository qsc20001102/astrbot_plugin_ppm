from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .database import Database
from .errors import NotFoundError, ValidationError


class WebApi:
    """Thin AstrBot Page API adapter; business rules stay in Database."""

    def __init__(self, database: Database, context: Any, config: dict[str, Any]):
        self.db = database
        self.context = context
        self.config = config

    def register(self, plugin_name: str) -> None:
        routes: list[tuple[str, Callable[..., Awaitable[Any]], list[str], str]] = [
            ("health", self.health, ["GET"], "PPM health"),
            ("dashboard", self.dashboard, ["GET"], "PPM dashboard"),
            ("members", self.members, ["GET"], "List team members"),
            ("members/save", self.save_member, ["POST"], "Create or update member"),
            ("members/delete", self.delete_member, ["POST"], "Delete member"),
            ("projects", self.projects, ["GET"], "List projects"),
            ("projects/save", self.save_project, ["POST"], "Create or update project"),
            ("projects/delete", self.delete_project, ["POST"], "Delete project"),
            (
                "projects/detail",
                self.project_detail,
                ["GET"],
                "Project traceability detail",
            ),
            (
                "projects/status-history/save",
                self.save_status_history,
                ["POST"],
                "Edit project status history",
            ),
            (
                "projects/status-history/add",
                self.add_status_history,
                ["POST"],
                "Add project status history",
            ),
            (
                "projects/status-history/delete",
                self.delete_status_history,
                ["POST"],
                "Delete project status history",
            ),
            (
                "projects/membership-history/save",
                self.save_membership_history,
                ["POST"],
                "Edit project membership history",
            ),
            (
                "projects/membership-history/add",
                self.add_membership_history,
                ["POST"],
                "Add project membership history",
            ),
            (
                "projects/membership-history/delete",
                self.delete_membership_history,
                ["POST"],
                "Delete project membership history",
            ),
            ("worklogs", self.work_logs, ["GET"], "List project daily logs"),
            ("worklogs/save", self.save_work_log, ["POST"], "Create project work log"),
            ("worklogs/delete", self.delete_work_log, ["POST"], "Delete project work log"),
            ("timeline", self.timeline, ["GET"], "Project daily timeline"),
            ("calendar", self.calendar, ["GET"], "Company work calendar"),
            (
                "calendar/save",
                self.save_calendar,
                ["POST"],
                "Override company calendar day",
            ),
            ("attendance", self.attendance, ["GET"], "Attendance matrix"),
            (
                "attendance/save",
                self.save_attendance,
                ["POST"],
                "Save attendance exception",
            ),
            (
                "attendance/summary",
                self.attendance_summary,
                ["GET"],
                "Attendance summary",
            ),
            (
                "ai/summary",
                self.ai_summary,
                ["POST"],
                "Generate multi-project daily summary",
            ),
        ]
        for endpoint, handler, methods, description in routes:
            self.context.register_web_api(
                f"/{plugin_name}/{endpoint}", handler, methods, description
            )

    async def _run(self, operation: Callable[[], Any]):
        try:
            return json_response(operation())
        except ValidationError as exc:
            return error_response(str(exc), status_code=400)
        except NotFoundError as exc:
            return error_response(str(exc), status_code=404)
        except Exception as exc:  # noqa: BLE001 - Page API must isolate plugin failures
            logger.exception("PPM Page API request failed")
            return error_response(f"操作失败：{exc}", status_code=500)

    async def health(self):
        return json_response({"ok": True, "version": 1})

    async def dashboard(self):
        return await self._run(self.db.dashboard)

    async def members(self):
        return await self._run(lambda: self.db.list_members())

    async def save_member(self):
        payload = await request.json(default={})
        return await self._run(lambda: self.db.save_member(payload))

    async def delete_member(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._deleted(self.db.delete_member, payload.get("id"))
        )

    async def projects(self):
        return await self._run(lambda: self.db.list_projects())

    async def save_project(self):
        payload = await request.json(default={})
        return await self._run(lambda: self.db.save_project(payload))

    async def delete_project(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._deleted(self.db.delete_project, payload.get("id"))
        )

    async def project_detail(self):
        return await self._run(lambda: self.db.project_detail(request.query.get("id")))

    async def save_work_log(self):
        payload = await request.json(default={})
        return await self._run(lambda: self.db.save_work_log(payload))

    async def work_logs(self):
        return await self._run(
            lambda: self.db.list_work_logs(
                request.query.get("project_id"), request.query.get("date")
            )
        )

    async def delete_work_log(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._deleted(self.db.delete_work_log, payload.get("id"))
        )

    async def save_status_history(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._saved(self.db.update_status_history, payload)
        )

    async def add_status_history(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._saved(self.db.insert_status_history, payload)
        )

    async def delete_status_history(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._deleted_pair(
                self.db.delete_status_history,
                payload.get("id"),
                payload.get("project_id"),
            )
        )

    async def save_membership_history(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._saved(self.db.update_membership_history, payload)
        )

    async def add_membership_history(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._saved(self.db.insert_membership_history, payload)
        )

    async def delete_membership_history(self):
        payload = await request.json(default={})
        return await self._run(
            lambda: self._deleted_pair(
                self.db.delete_membership_history,
                payload.get("id"),
                payload.get("project_id"),
            )
        )

    async def timeline(self):
        today = _today()
        start = request.query.get("start") or (today - timedelta(days=6)).isoformat()
        end = request.query.get("end") or today.isoformat()
        return await self._run(lambda: self.db.timeline(start, end))

    async def calendar(self):
        month = request.query.get("month") or _today().strftime("%Y-%m")
        return await self._run(lambda: self.db.get_calendar(month))

    async def save_calendar(self):
        payload = await request.json(default={})
        return await self._run(lambda: self._saved(self.db.save_calendar_day, payload))

    async def attendance(self):
        month = request.query.get("month") or _today().strftime("%Y-%m")
        return await self._run(lambda: self.db.attendance_matrix(month))

    async def save_attendance(self):
        payload = await request.json(default={})
        return await self._run(lambda: self._saved(self.db.save_attendance, payload))

    async def attendance_summary(self):
        today = _today()
        start = request.query.get("start") or today.replace(day=1).isoformat()
        end = request.query.get("end") or today.isoformat()
        return await self._run(lambda: self.db.attendance_summary(start, end))

    async def ai_summary(self):
        payload = await request.json(default={})
        provider_id = str(self.config.get("ai_provider_id", "")).strip()
        if not provider_id:
            return error_response("请先在插件配置中选择日报总结模型", status_code=400)
        try:
            project_ids, material = self.db.team_summary_material(
                payload.get("project_ids"), payload.get("date") or _today().isoformat()
            )
            prompt = f"{self.config.get('ai_summary_prompt', '')}\n\n以下是项目资料：\n{material}"
            response = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )
            content = str(response.completion_text).strip()
            if not content:
                raise RuntimeError("模型返回了空内容")
            summary_date = payload.get("date") or _today().isoformat()
            self.db.save_team_summary(project_ids, summary_date, provider_id, content)
            return json_response(
                {"content": content, "date": summary_date, "project_ids": project_ids}
            )
        except (ValidationError, NotFoundError) as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures for WebUI
            logger.exception("PPM AI summary generation failed")
            return error_response(f"AI 日报生成失败：{exc}", status_code=502)

    @staticmethod
    def _deleted(operation: Callable[[Any], None], entity_id: Any) -> dict[str, bool]:
        operation(entity_id)
        return {"deleted": True}

    @staticmethod
    def _saved(operation: Callable[[Any], None], payload: Any) -> dict[str, bool]:
        operation(payload)
        return {"saved": True}

    @staticmethod
    def _deleted_pair(
        operation: Callable[[Any, Any], None], first: Any, second: Any
    ) -> dict[str, bool]:
        operation(first, second)
        return {"deleted": True}


def _today():
    return datetime.now().astimezone().date()
