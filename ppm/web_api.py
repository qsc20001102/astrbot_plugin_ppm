from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from functools import wraps
from typing import Any

from astrbot.api import logger
from astrbot.api.web import error_response, json_response, request

from .database import Database
from .errors import NotFoundError, ValidationError
from .validation import parse_date


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
            ("todos", self.todos, ["GET"], "List project todos and reminder status"),
            ("todos/save", self.save_todo, ["POST"], "Create or update todo"),
            ("todos/delete", self.delete_todo, ["POST"], "Delete todo"),
            ("ai/project-summary", self.ai_project_summary, ["POST"], "Generate project lifecycle summary"),
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
            ("summaries", self.summaries, ["GET"], "List daily summaries"),
            (
                "summaries/save",
                self.save_summary_content,
                ["POST"],
                "Update saved daily summary content",
            ),
            (
                "summaries/delete",
                self.delete_summary,
                ["POST"],
                "Delete daily summary",
            ),
        ]
        for endpoint, handler, methods, description in routes:
            self.context.register_web_api(
                f"/{plugin_name}/{endpoint}", self._guard(handler), methods, description
            )

    @staticmethod
    def _guard(handler):
        @wraps(handler)
        async def guarded():
            try:
                return await handler()
            except ValidationError as exc:
                return error_response(str(exc), status_code=400)
        return guarded

    @staticmethod
    async def _payload() -> dict[str, Any]:
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            raise ValidationError("请求内容必须是 JSON 对象")
        return payload

    async def _run(self, operation: Callable[[], Any]):
        try:
            return json_response(await asyncio.to_thread(operation))
        except ValidationError as exc:
            return error_response(str(exc), status_code=400)
        except NotFoundError as exc:
            return error_response(str(exc), status_code=404)
        except Exception as exc:  # noqa: BLE001 - Page API must isolate plugin failures
            logger.exception("PPM Page API request failed")
            return error_response(f"操作失败：{exc}", status_code=500)

    async def health(self):
        return json_response({"ok": True, "version": 1, "settings": {
            "company_name": self.config.get("company_name") or "我的团队",
            "week_start": "sunday" if self.config.get("week_start") == "sunday" else "monday",
        }})

    async def dashboard(self):
        return await self._run(self.db.dashboard)

    async def members(self):
        return await self._run(lambda: self.db.list_members())

    async def save_member(self):
        payload = await self._payload()
        return await self._run(lambda: self.db.save_member(payload))

    async def delete_member(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._deleted(self.db.delete_member, payload.get("id"))
        )

    async def projects(self):
        summary_date = request.query.get("summary_date")
        if summary_date is not None:
            return await self._run(lambda: self.db.summary_projects(summary_date))
        return await self._run(lambda: self.db.list_projects())

    async def todos(self):
        configured = bool(str(self.config.get("todo_push_session", "") or "").strip())
        return await self._run(lambda: {"todos": self.db.list_todos(), "push_configured": configured})

    async def save_todo(self):
        payload = await self._payload()
        return await self._run(lambda: self.db.save_todo(payload))

    async def delete_todo(self):
        payload = await self._payload()
        return await self._run(lambda: self._deleted(self.db.delete_todo, payload.get("id")))

    async def save_project(self):
        payload = await self._payload()
        return await self._run(lambda: self.db.save_project(payload))

    async def delete_project(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._deleted(self.db.delete_project, payload.get("id"))
        )

    async def project_detail(self):
        return await self._run(lambda: self.db.project_detail(request.query.get("id")))

    async def save_work_log(self):
        payload = await self._payload()
        return await self._run(lambda: self.db.save_work_log(payload))

    async def work_logs(self):
        return await self._run(
            lambda: self.db.list_work_logs(
                request.query.get("project_id"), request.query.get("date")
            )
        )

    async def delete_work_log(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._deleted(self.db.delete_work_log, payload.get("id"))
        )

    async def save_status_history(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._saved(self.db.update_status_history, payload)
        )

    async def add_status_history(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._saved(self.db.insert_status_history, payload)
        )

    async def delete_status_history(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._deleted_pair(
                self.db.delete_status_history,
                payload.get("id"),
                payload.get("project_id"),
            )
        )

    async def save_membership_history(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._saved(self.db.update_membership_history, payload)
        )

    async def add_membership_history(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._saved(self.db.insert_membership_history, payload)
        )

    async def delete_membership_history(self):
        payload = await self._payload()
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
        payload = await self._payload()
        return await self._run(lambda: self._saved(self.db.save_calendar_day, payload))

    async def attendance(self):
        month = request.query.get("month") or _today().strftime("%Y-%m")
        return await self._run(lambda: self.db.attendance_matrix(month))

    async def save_attendance(self):
        payload = await self._payload()
        return await self._run(lambda: self._saved(self.db.save_attendance, payload))

    async def attendance_summary(self):
        today = _today()
        start = request.query.get("start") or today.replace(day=1).isoformat()
        end = request.query.get("end") or today.isoformat()
        return await self._run(lambda: self.db.attendance_summary(start, end))

    async def generate_summary(
        self,
        project_ids: Any,
        summary_date: Any,
        history_days: Any = 1,
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Generate a summary and optionally persist it for the given date."""
        provider_id = str(self.config.get("ai_provider_id", "")).strip()
        if not provider_id:
            raise ValidationError("请先在插件配置中选择日报总结模型")
        summary_date = parse_date(summary_date, "summary_date")
        project_ids, material = await asyncio.to_thread(
            self.db.team_summary_material, project_ids, summary_date, history_days
        )
        prompt = f"{self.config.get('ai_summary_prompt', '')}\n\n以下是项目资料：\n{material}"
        content = await self._generate_text(provider_id, prompt)
        persisted = await asyncio.to_thread(
            self.db.save_team_summary,
            project_ids,
            summary_date,
            provider_id,
            content,
            overwrite=overwrite,
        )
        return {
            "content": content,
            "date": summary_date,
            "project_ids": project_ids,
            "persisted": persisted,
        }

    async def generate_project_summary(self, project_id: Any) -> str:
        material = await asyncio.to_thread(self.db.project_lifecycle_material, project_id)
        provider_id = str(self.config.get("ai_provider_id", "")).strip()
        if not provider_id:
            raise ValidationError("请先在插件配置中选择日报总结模型")
        prompt = f"{self.config.get('ai_project_summary_prompt', '')}\n\n以下是项目全生命周期资料：\n{material}"
        return await self._generate_text(provider_id, prompt)

    async def _generate_text(self, provider_id: str, prompt: str) -> str:
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
        )
        content = response.completion_text
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("模型返回了空内容")
        return content.strip()

    async def ai_project_summary(self):
        payload = await self._payload()
        try:
            content = await self.generate_project_summary(payload.get("project_id"))
            return json_response({"content": content})
        except (ValidationError, NotFoundError) as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures for WebUI
            logger.exception("PPM project lifecycle summary generation failed")
            return error_response(f"项目总结生成失败：{exc}", status_code=502)

    async def ai_summary(self):
        payload = await self._payload()
        try:
            overwrite = payload.get("overwrite", False)
            if not isinstance(overwrite, bool):
                raise ValidationError("overwrite 必须是布尔值")
            result = await self.generate_summary(
                payload.get("project_ids"),
                payload.get("date") or _today().isoformat(),
                payload.get("history_days", 1),
                overwrite=overwrite,
            )
            return json_response(result)
        except (ValidationError, NotFoundError) as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures for WebUI
            logger.exception("PPM AI summary generation failed")
            return error_response(f"AI 日报生成失败：{exc}", status_code=502)

    async def summaries(self):
        today = _today()
        return await self._run(
            lambda: self.db.list_team_summaries(
                request.query.get("start")
                or (today - timedelta(days=6)).isoformat(),
                request.query.get("end") or today.isoformat(),
            )
        )

    async def save_summary_content(self):
        payload = await self._payload()
        return await self._run(lambda: self._save_summary(payload))

    async def delete_summary(self):
        payload = await self._payload()
        return await self._run(
            lambda: self._deleted(
                self.db.delete_team_summary,
                payload.get("date") or _today().isoformat(),
            )
        )

    def _save_summary(self, payload: dict[str, Any]) -> dict[str, Any]:
        summary_date = payload.get("date") or _today().isoformat()
        if "project_ids" not in payload:
            return self.db.update_team_summary_content(
                summary_date, payload.get("content")
            )
        project_ids, _ = self.db.team_summary_material(
            payload.get("project_ids"), summary_date, 0
        )
        self.db.save_team_summary(
            project_ids,
            summary_date,
            str(self.config.get("ai_provider_id", "")).strip(),
            payload.get("content"),
            overwrite=True,
        )
        return self.db.get_team_summary(summary_date)

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
