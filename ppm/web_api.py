from __future__ import annotations

import asyncio
import hashlib
import json
import time
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import wraps
from typing import Any

import logging
from aiohttp import web

logger = logging.getLogger(__name__)

def json_response(data):
    return web.json_response({"data": data})

def error_response(message, status_code=400):
    return web.json_response({"error": message}, status=status_code)

from .database import Database
from .errors import NotFoundError, ValidationError
from .validation import parse_date
from .llm import ModelClient
from .summary_prompts import RULES, TEMPLATES


class WebApi:
    """Standalone HTTP API; business rules stay in Database."""

    def __init__(self, database: Database, config, model):
        self.db = database
        self.model = model
        self.config = config

    def register(self, app) -> None:
        routes = [
            ("ai/preview", self.summary_preview, ["POST"], "Preview summary inputs"),
            ("ai/templates", self.summary_templates, ["GET"], "Summary templates"),
            ("settings/diagnose", self.diagnose_model, ["POST"], "Diagnose saved model"),
            ("settings", self.settings, ["GET"], "Settings"),
            ("settings/save", self.save_settings, ["POST"], "Save settings"),
            ("settings/test", self.test_model, ["POST"], "Test saved model"),
            ("settings/models", self.list_models, ["POST"], "Discover available models"),
            ("todos", self.todos, ["GET"], "List notes and completion state"),
            ("todos/save", self.save_todo, ["POST"], "Save todo"),
            ("todos/delete", self.delete_todo, ["POST"], "Delete todo"),
            ("health", self.health, ["GET"], "PPM health"),
            ("dashboard", self.dashboard, ["GET"], "PPM dashboard"),
            ("members", self.members, ["GET"], "List team members"),
            ("members/save", self.save_member, ["POST"], "Create or update member"),
            ("members/delete", self.delete_member, ["POST"], "Delete member"),
            ("projects", self.projects, ["GET"], "List projects"),
            ("tasks", self.tasks, ["GET"], "Project tasks and lifecycle history"),
            ("tasks/save", self.save_task, ["POST"], "Create or update project task"),
            ("tasks/delete", self.delete_task, ["POST"], "Soft delete project task"),
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
            for method in methods:
                app.router.add_route(method, f"/api/{endpoint}", self._guard(handler))

    @staticmethod
    def _guard(handler):
        @wraps(handler)
        async def guarded(request):
            try:
                return await handler(request)
            except ValidationError as exc:
                return error_response(str(exc), status_code=400)
        return guarded

    @staticmethod
    async def _payload(request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationError("请求内容必须是有效 JSON") from exc
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
        except Exception:  # Keep unexpected internal errors out of HTTP responses.
            logger.exception("PPM Page API request failed")
            return error_response("操作失败，请查看服务日志", status_code=500)

    async def health(self, request):
        return json_response({"ok": True, "version": 1, "settings": {
            "week_start": "sunday" if self.config.get("week_start") == "sunday" else "monday",
            "ui_color_theme": self.config.get("ui_color_theme") if self.config.get("ui_color_theme") in ("forest", "ocean", "violet", "amber") else "forest",
        }})

    async def settings(self, request):
        return json_response(self.config.public())

    async def save_settings(self, request):
        payload = await self._payload(request)
        return json_response(self.config.save(payload))

    async def test_model(self, request):
        await self._payload(request)
        return json_response({"reply": await self.model.generate("请只回复：连接成功")})

    async def list_models(self, request):
        payload = await self._payload(request)
        config = self.config.resolve({k: payload[k] for k in
            ("ai_base_url", "ai_api_key", "ai_timeout", "clear_api_key") if k in payload})
        if (config["ai_base_url"].rstrip("/") != self.config["ai_base_url"].rstrip("/")
                and self.config.get("ai_api_key") and "ai_api_key" not in payload
                and payload.get("clear_api_key") is not True):
            raise ValidationError("API 地址已更改，请填写该服务的密钥；免密服务请明确填写空密钥")
        return json_response({"models": await ModelClient(config).list_models()})

    async def todos(self, request):
        return await self._run(self.db.list_todos)

    async def save_todo(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self.db.save_todo(payload))

    async def delete_todo(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self._deleted(self.db.delete_todo, payload.get("id")))

    async def dashboard(self, request):
        return await self._run(self.db.dashboard)

    async def members(self, request):
        return await self._run(lambda: self.db.list_members())

    async def save_member(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self.db.save_member(payload))

    async def delete_member(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._deleted(self.db.delete_member, payload.get("id"))
        )

    async def projects(self, request):
        summary_date = request.query.get("summary_date")
        if summary_date is not None:
            return await self._run(lambda: self.db.summary_projects(summary_date))
        return await self._run(lambda: self.db.list_projects())

    async def tasks(self, request):
        return await self._run(lambda: self.db.list_tasks(request.query.get("project_id")))

    async def save_task(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self.db.save_task(payload))

    async def delete_task(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self._deleted_pair(self.db.delete_task, payload.get("id"), payload.get("project_id")))

    async def save_project(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self.db.save_project(payload))

    async def delete_project(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._deleted(self.db.delete_project, payload.get("id"))
        )

    async def project_detail(self, request):
        return await self._run(lambda: self.db.project_detail(request.query.get("id")))

    async def save_work_log(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self.db.save_work_log(payload))

    async def work_logs(self, request):
        return await self._run(
            lambda: self.db.list_work_logs(
                request.query.get("project_id"), request.query.get("date")
            )
        )

    async def delete_work_log(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._deleted(self.db.delete_work_log, payload.get("id"))
        )

    async def save_status_history(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._saved(self.db.update_status_history, payload)
        )

    async def add_status_history(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._saved(self.db.insert_status_history, payload)
        )

    async def delete_status_history(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._deleted_pair(
                self.db.delete_status_history,
                payload.get("id"),
                payload.get("project_id"),
            )
        )

    async def save_membership_history(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._saved(self.db.update_membership_history, payload)
        )

    async def add_membership_history(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._saved(self.db.insert_membership_history, payload)
        )

    async def delete_membership_history(self, request):
        payload = await self._payload(request)
        return await self._run(
            lambda: self._deleted_pair(
                self.db.delete_membership_history,
                payload.get("id"),
                payload.get("project_id"),
            )
        )

    async def timeline(self, request):
        today = _today()
        start = request.query.get("start") or (today - timedelta(days=6)).isoformat()
        end = request.query.get("end") or today.isoformat()
        return await self._run(lambda: self.db.timeline(start, end))

    async def calendar(self, request):
        month = request.query.get("month") or _today().strftime("%Y-%m")
        return await self._run(lambda: self.db.get_calendar(month))

    async def save_calendar(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self._saved(self.db.save_calendar_day, payload))

    async def attendance(self, request):
        month = request.query.get("month") or _today().strftime("%Y-%m")
        return await self._run(lambda: self.db.attendance_matrix(month))

    async def save_attendance(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self._saved(self.db.save_attendance, payload))

    async def attendance_summary(self, request):
        today = _today()
        start = request.query.get("start") or today.replace(day=1).isoformat()
        end = request.query.get("end") or today.isoformat()
        return await self._run(lambda: self.db.attendance_summary(start, end))

    async def summary_templates(self, request):
        return json_response(TEMPLATES)

    async def prepare_summary(self, project_ids, summary_date, history_days=1, *, period="daily", template="custom"):
        config = dict(self.config)
        if not config.get("ai_model"):
            raise ValidationError("请先在模型配置中填写日报总结模型")
        if period not in ("daily", "weekly") or template not in ("custom", *TEMPLATES):
            raise ValidationError("无效的汇报类型或模板")
        end = parse_date(summary_date, "summary_date")
        end_day = datetime.strptime(end, "%Y-%m-%d").date()
        start_day = end_day - timedelta(days=6 if period == "weekly" else 0)
        project_ids, end_material = await asyncio.to_thread(self.db.team_summary_material,
            project_ids, end, history_days if period == "daily" else 0)
        parts = []
        for offset in range((end_day - start_day).days + 1):
            day = (start_day + timedelta(days=offset)).isoformat()
            if day == end:
                material = end_material
            else:
                available = await asyncio.to_thread(self.db.summary_projects, day)
                day_ids = [p["id"] for p in available if p["id"] in project_ids]
                if not day_ids:
                    parts.append(f"日期：{day}：所选项目尚无历史档案。")
                    continue
                _, material = await asyncio.to_thread(self.db.team_summary_material, day_ids, day, 0)
            parts.append(f"日期：{day}\n{material}")
        writing = config.get("ai_summary_prompt", "") if template == "custom" else TEMPLATES[template]["prompt"]
        system_prompt = f"资料解读规则：{RULES}\n\n写作要求：\n{writing}"
        material = f"汇报范围：{start_day.isoformat()} 至 {end}\n\n" + "\n\n".join(parts)
        body = ModelClient.completion_body(config, material, system_prompt)
        # Include credentials only in the fingerprint input, never in the public preview.
        fingerprint = hashlib.sha256(json.dumps({"config": config, "body": body},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        preview = {"model": config["ai_model"], "endpoint": config["ai_base_url"].rstrip("/") + "/chat/completions",
            "start": start_day.isoformat(), "date": end, "period": period, "template": template,
            "history_days": history_days if period == "daily" else 0,
            "project_ids": project_ids, "writing_prompt": writing, "rules": RULES,
            "material": material, "request": body, "fingerprint": fingerprint}
        return config, preview

    async def summary_preview(self, request):
        payload = await self._payload(request)
        _, preview = await self.prepare_summary(payload.get("project_ids"), payload.get("date") or _today().isoformat(),
            payload.get("history_days", 1), period=payload.get("period", "daily"), template=payload.get("template", "custom"))
        return json_response(preview)

    async def generate_summary(self, project_ids, summary_date, history_days=1, *, overwrite=False,
                               period="daily", template="custom", fingerprint=None):
        config, preview = await self.prepare_summary(project_ids, summary_date, history_days, period=period, template=template)
        if fingerprint is not None and fingerprint != preview["fingerprint"]:
            raise ValidationError("模型配置或工作资料已变化，请重新核对资料后生成")
        # Snapshot guarantees the model and instructions match the reviewed inputs.
        content = await ModelClient(config).generate(preview["material"], system_prompt=preview["request"]["messages"][0]["content"])
        persisted = False
        if period == "daily":
            persisted = await asyncio.to_thread(self.db.save_team_summary, preview["project_ids"], preview["date"],
                config["ai_model"], content, overwrite=overwrite)
        return {"content": content, "date": preview["date"], "start": preview["start"], "period": period,
                "project_ids": preview["project_ids"], "persisted": persisted,
                "model": config["ai_model"], "fingerprint": preview["fingerprint"]}

    async def diagnose_model(self, request):
        payload = await self._payload(request)
        kind = payload.get("kind", "generation")
        if kind not in ("models", "generation"):
            raise ValidationError("请选择模型列表或文本生成诊断")
        config = dict(self.config)
        client = ModelClient(config)
        prompt = "请只回复：连接成功"
        detail = {"kind": kind, "model": config["ai_model"], "ok": False, "http_status": None,
            "error_code": None, "request_id": None, "retry_after": None,
            "request": {"method": "GET" if kind == "models" else "POST",
                "url": config["ai_base_url"].rstrip("/") + ("/models" if kind == "models" else "/chat/completions"),
                "headers": {"Authorization": "Bearer [已隐藏]"} if config.get("ai_api_key") else {},
                "body": None if kind == "models" else client.completion_body(config, prompt)}}
        started = time.perf_counter()
        try:
            if kind == "models":
                models = await client.list_models(diagnostics=detail)
                detail.update({"model_count": len(models), "selected_model_listed": config["ai_model"] in models})
            else:
                detail["reply"] = await client.generate(prompt, diagnostics=detail)
            detail["ok"] = True
        except ValidationError as exc:
            detail.update({"error": str(exc), "error_code": getattr(exc, "provider_code", None)})
        detail["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        def redact(value):
            if isinstance(value, dict):
                return {k: redact(v) for k, v in value.items()}
            if isinstance(value, str):
                if config.get("ai_api_key"):
                    value = value.replace(config["ai_api_key"], "[已隐藏]")
                return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏]", value)
            return value
        return json_response(redact(detail))

    async def ai_summary(self, request):
        payload = await self._payload(request)
        try:
            overwrite = payload.get("overwrite", False)
            if not isinstance(overwrite, bool):
                raise ValidationError("overwrite 必须是布尔值")
            result = await self.generate_summary(
                payload.get("project_ids"),
                payload.get("date") or _today().isoformat(),
                payload.get("history_days", 1),
                overwrite=overwrite,
                period=payload.get("period", "daily"), template=payload.get("template", "custom"),
                fingerprint=payload.get("fingerprint"),
            )
            return json_response(result)
        except (ValidationError, NotFoundError) as exc:
            return error_response(str(exc), status_code=400)
        except Exception as exc:  # noqa: BLE001 - normalize provider failures for WebUI
            logger.exception("PPM AI summary generation failed")
            return error_response(f"AI 日报生成失败：{exc}", status_code=502)

    async def summaries(self, request):
        today = _today()
        return await self._run(
            lambda: self.db.list_team_summaries(
                request.query.get("start")
                or (today - timedelta(days=6)).isoformat(),
                request.query.get("end") or today.isoformat(),
            )
        )

    async def save_summary_content(self, request):
        payload = await self._payload(request)
        return await self._run(lambda: self._save_summary(payload))

    async def delete_summary(self, request):
        payload = await self._payload(request)
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
            str(self.config.get("ai_model", "")).strip(),
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
