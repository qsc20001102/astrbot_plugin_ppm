from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .database import Database


class TodoReminderService:
    """Persistent one-shot and weekly reminders, independent of AstrBot's message adapters."""

    def __init__(
        self, database: Database, config: Any,
        send: Callable[[str, str], Awaitable[bool]], logger: Any,
    ):
        self.db = database
        self.config = config
        self.send = send
        self.logger = logger
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="ppm-todo-reminders")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            # Let an in-flight send finish and persist its result before reloading.
            await self._task
            self._task = None

    async def run_once(self) -> None:
        for _ in range(20):
            if self._stop.is_set():
                return
            todo = await asyncio.to_thread(self.db.claim_due_todo)
            if todo is None:
                return
            error = ""
            try:
                target = str(self.config.get("todo_push_session", "") or "").strip()
                if not target:
                    raise ValueError("请在插件配置中填写待办推送会话 ID（UMO）")
                text = (
                    f"待办提醒 #{todo['id']}\n项目：{todo['project_name']}（ID：{todo['project_id']}）"
                    f"\n待办内容：\n{todo['content']}"
                )
                matched = await asyncio.wait_for(self.send(target, text), timeout=30)
                if not matched:
                    raise RuntimeError("未找到对应消息平台，请检查会话 ID 和平台连接")
            except TimeoutError:
                error = "推送超时，将在一分钟后重试"
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            await asyncio.to_thread(self.db.finish_todo_push, todo["id"], todo["claim_token"], error)
            if error:
                self.logger.warning("PPM 待办 %s 推送失败：%s", todo["id"], error)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.run_once()
            except Exception:
                self.logger.exception("PPM 待办推送轮询失败")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=15)
            except TimeoutError:
                pass
