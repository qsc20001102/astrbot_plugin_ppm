import asyncio
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from ppm.todo_schedule import next_weekly

from ppm.database import Database
from ppm.errors import NotFoundError, ValidationError
from ppm.todo_service import TodoReminderService


class TodoDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "ppm.sqlite3")
        self.db.initialize()
        self.project = self.db.save_project({"name": "待办项目"})

    def tearDown(self):
        self.temp_dir.cleanup()

    def save(self, **changes):
        return self.db.save_todo({"project_id": self.project["id"], "content": "第一行\n第二行", **changes})

    def due(self, **changes):
        return self.save(push_enabled=True, push_at="2026-01-01T09:00:00+08:00", **changes)

    def test_create_edit_filter_data_and_soft_delete(self):
        todo = self.save()
        self.assertNotIn("status", todo)
        self.assertEqual(todo["push_enabled"], 0)
        self.assertEqual(self.db.list_todos()[0]["project_name"], "待办项目")
        self.save(id=todo["id"], content="修订")
        self.assertEqual(self.db.list_todos()[0]["content"], "修订")
        self.db.delete_todo(todo["id"])
        self.assertEqual(self.db.list_todos(), [])
        with self.db._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM todos").fetchone()[0], 1)
        with self.assertRaises(NotFoundError):
            self.db.delete_todo(todo["id"])

    def test_invalid_input_never_creates_a_todo(self):
        for fields in ({"project_id": 999}, {"content": None}, {"content": " "},
                       {"content": "x" * 3001}, {"push_mode": "unknown"},
                       {"push_enabled": "true"}, {"push_enabled": True},
                       {"push_at": "2026-01-01T10:00:00"}, {"push_at": "invalid"}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.save(**fields)
        self.assertEqual(self.db.list_todos(), [])

    def test_timezones_are_normalized_and_future_not_sent(self):
        todo = self.due()
        self.assertEqual(todo["push_at"], "2026-01-01T01:00:00+00:00")
        self.assertIsNone(self.db.claim_due_todo(todo["push_due"] - 1))
        self.assertEqual(self.db.claim_due_todo(todo["push_due"])["id"], todo["id"])

    def test_disabled_and_deleted_do_not_send(self):
        self.save()
        todo = self.due()
        self.db.delete_todo(todo["id"])
        self.assertIsNone(self.db.claim_due_todo())

    def test_deleted_project_stops_push_and_keeps_todo_visible(self):
        todo = self.due()
        self.db.delete_project(self.project["id"])
        self.assertIsNone(self.db.claim_due_todo())
        self.assertTrue(self.db.list_todos()[0]["project_deleted_at"])
        with self.assertRaises(ValidationError):
            self.due(id=todo["id"])
        self.save(id=todo["id"], push_enabled=False)
        self.assertEqual(self.db.list_todos()[0]["push_enabled"], 0)

    def test_only_one_worker_claims_and_edits_wait_for_send(self):
        todo = self.due()
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: self.db.claim_due_todo(), range(4)))
        claimed = [claim for claim in claims if claim]
        self.assertEqual(len(claimed), 1)
        for operation in (lambda: self.save(id=todo["id"]),
                          lambda: self.db.delete_todo(todo["id"])):
            with self.assertRaises(ValidationError):
                operation()
        self.db.finish_todo_push(todo["id"], claimed[0]["claim_token"])
        self.assertIsNone(self.db.claim_due_todo())
        self.assertTrue(self.db.list_todos()[0]["sent_at"])

    def test_failure_retries_and_stale_acknowledgement_is_ignored(self):
        self.due()
        claim = self.db.claim_due_todo()
        self.db.finish_todo_push(claim["id"], claim["claim_token"], "网络错误")
        self.assertIsNone(self.db.claim_due_todo())
        retry = self.db.claim_due_todo(time.time() + 61)
        self.assertEqual(retry["push_error"], "网络错误")
        self.db.finish_todo_push(claim["id"], claim["claim_token"])
        self.assertIsNone(self.db.list_todos()[0]["sent_at"])
        self.db.finish_todo_push(retry["id"], retry["claim_token"])
        self.assertTrue(self.db.list_todos()[0]["sent_at"])
        self.assertEqual(self.db.list_todos()[0]["push_error"], "")

    def test_sent_reminder_survives_restart_and_content_edits(self):
        todo = self.due()
        claim = self.db.claim_due_todo()
        self.db.finish_todo_push(todo["id"], claim["claim_token"])
        self.db = Database(self.db.path)
        self.db.initialize()
        self.due(id=todo["id"], content="修改内容")
        self.assertIsNone(self.db.claim_due_todo())
        self.save(id=todo["id"], push_enabled=True, push_at="2026-01-02T10:00:00+08:00")
        self.assertIsNotNone(self.db.claim_due_todo())

    def test_interrupted_worker_can_be_reclaimed_after_lease(self):
        self.due()
        first = self.db.claim_due_todo()
        self.db = Database(self.db.path)
        self.db.initialize()
        self.assertIsNone(self.db.claim_due_todo())
        second = self.db.claim_due_todo(time.time() + 121)
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["claim_token"], second["claim_token"])

    def test_reenable_behavior(self):
        todo = self.due()
        self.save(id=todo["id"], push_enabled=False)
        self.assertIsNone(self.db.claim_due_todo())
        self.due(id=todo["id"])
        self.assertIsNotNone(self.db.claim_due_todo())

    def test_old_database_gains_todo_table_without_losing_projects(self):
        with self.db._connection() as conn:
            conn.execute("DROP TABLE todos")
        self.db = Database(self.db.path)
        self.db.initialize()
        self.assertEqual(self.db.list_projects()[0]["id"], self.project["id"])
        self.save()
        self.assertEqual(len(self.db.list_todos()), 1)

    def test_expired_claim_cannot_acknowledge_an_edited_schedule(self):
        todo = self.due()
        claim = self.db.claim_due_todo()
        with self.db._connection() as conn:
            conn.execute("UPDATE todos SET claimed_until=0 WHERE id=?", (todo["id"],))
        self.save(id=todo["id"], push_enabled=True, push_at="2030-01-01T10:00:00+08:00")
        self.db.finish_todo_push(todo["id"], claim["claim_token"])
        self.assertIsNone(self.db.list_todos()[0]["sent_at"])

    def weekly(self, **changes):
        return self.save(push_enabled=True, push_mode="weekly", push_weekdays=[0, 2, 4],
                         push_time="09:00", push_utc_offset=480, **changes)

    def test_weekly_validation(self):
        defaults = {"push_mode": "weekly", "push_weekdays": [0], "push_time": "09:00", "push_utc_offset": 480}
        for fields in ({"push_weekdays": []}, {"push_weekdays": "1"}, {"push_weekdays": [7]},
                       {"push_weekdays": [True]}, {"push_time": "24:00"}, {"push_time": "9:00"},
                       {"push_utc_offset": 1000}, {"push_utc_offset": True}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                self.save(**{**defaults, **fields})

    def test_weekly_next_occurrence_and_week_wrap(self):
        monday = datetime(2030, 1, 7, 1, tzinfo=timezone.utc).timestamp()
        self.assertEqual(next_weekly(monday-1, [0,2,4], "09:00", 480), monday)
        self.assertEqual(next_weekly(monday, [0,2,4], "09:00", 480), monday+2*86400)
        self.assertEqual(next_weekly(monday, [0], "09:00", 480), monday+7*86400)
        self.assertEqual(next_weekly(monday, list(range(7)), "09:00", 480), monday+86400)
        # UTC Sunday is already local Monday at UTC+14.
        self.assertEqual(next_weekly(monday-8*3600-1, [0], "09:00", 840), monday-6*3600)

    def test_weekly_success_schedules_next_and_content_edit_preserves_it(self):
        monday = datetime(2030, 1, 7, 1, tzinfo=timezone.utc).timestamp()
        with patch("ppm.database.time.time", return_value=monday-60):
            todo = self.weekly()
        claim = self.db.claim_due_todo(monday)
        with patch("ppm.database.time.time", return_value=monday+1):
            self.db.finish_todo_push(todo["id"], claim["claim_token"])
            self.weekly(id=todo["id"], content="修改内容")
        row = self.db.list_todos()[0]
        self.assertEqual(row["push_due"], monday+2*86400)
        self.assertTrue(row["sent_at"])
        self.assertIsNone(self.db.claim_due_todo(monday+2))
        self.assertIsNotNone(self.db.claim_due_todo(monday+2*86400))

    def test_weekly_restart_skips_missed_days(self):
        monday = datetime(2030, 1, 7, 1, tzinfo=timezone.utc).timestamp()
        with patch("ppm.database.time.time", return_value=monday-60):
            self.weekly()
        self.db = Database(self.db.path)
        self.db.initialize()
        # Restart Tuesday: do not send Monday's expired reminder.
        self.assertIsNone(self.db.claim_due_todo(monday+86400))
        self.assertEqual(self.db.list_todos()[0]["push_due"], monday+2*86400)
        self.assertIsNotNone(self.db.claim_due_todo(monday+2*86400))

    def test_weekly_failure_retries_same_occurrence(self):
        monday = datetime(2030, 1, 7, 1, tzinfo=timezone.utc).timestamp()
        with patch("ppm.database.time.time", return_value=monday-60):
            self.weekly()
        claim = self.db.claim_due_todo(monday)
        with patch("ppm.database.time.time", return_value=monday):
            self.db.finish_todo_push(claim["id"], claim["claim_token"], "网络错误")
        self.assertIsNone(self.db.claim_due_todo(monday+59))
        retry = self.db.claim_due_todo(monday+60)
        self.assertEqual(retry["push_due"], monday)
        with patch("ppm.database.time.time", return_value=monday+60):
            self.db.finish_todo_push(retry["id"], retry["claim_token"])
        self.assertEqual(self.db.list_todos()[0]["push_due"], monday+2*86400)

    def test_legacy_states_are_removed_and_stopped_reminders_stay_off(self):
        with self.db._connection() as conn:
            conn.execute("DROP TABLE todos")
            conn.execute("""CREATE TABLE todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                content TEXT NOT NULL, status TEXT NOT NULL DEFAULT '未开始',
                push_enabled INTEGER NOT NULL DEFAULT 0, push_at TEXT, push_due REAL,
                sent_at TEXT, push_error TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0,
                claim_token TEXT, claimed_until REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT)""")
            for status in ("未开始", "已完成", "已取消"):
                conn.execute("INSERT INTO todos(project_id,content,status,push_enabled,push_due,created_at,updated_at) VALUES(?,?,?,1,1,'now','now')", (self.project["id"],status,status))
        self.db = Database(self.db.path)
        self.db.initialize()
        rows = self.db.list_todos()
        self.assertEqual(len(rows),3)
        self.assertTrue(all("status" not in row and row["push_mode"]=="once" for row in rows))
        self.assertEqual([row["push_enabled"] for row in rows], [0,0,1])
        self.assertEqual(self.db.claim_due_todo()["content"],"未开始")
        # Initialization is repeatable after migration.
        Database(self.db.path).initialize()



class TodoReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "ppm.sqlite3")
        self.db.initialize()
        project = self.db.save_project({"name": "提醒项目"})
        self.todo = self.db.save_todo({"project_id": project["id"], "content": "准备验收",
            "push_enabled": True, "push_at": (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()})
        self.send = AsyncMock(return_value=True)
        self.config = {"todo_push_session": "platform:GroupMessage:123"}
        self.service = TodoReminderService(self.db, self.config, self.send, Mock())

    async def asyncTearDown(self):
        await self.service.stop()
        self.temp_dir.cleanup()

    async def test_send_uses_configured_session_and_runs_once(self):
        await self.service.run_once()
        await self.service.run_once()
        self.send.assert_awaited_once()
        target, text = self.send.call_args.args
        self.assertEqual(target, self.config["todo_push_session"])
        self.assertIn("提醒项目", text)
        self.assertIn("准备验收", text)
        self.assertNotIn("状态：", text)
        self.assertTrue(self.db.list_todos()[0]["sent_at"])

    async def test_missing_session_does_not_send_and_explains_failure(self):
        self.config["todo_push_session"] = ""
        await self.service.run_once()
        self.send.assert_not_awaited()
        self.assertIn("会话", self.db.list_todos()[0]["push_error"])

    async def test_unmatched_platform_is_not_marked_as_sent(self):
        self.send.return_value = False
        await self.service.run_once()
        row = self.db.list_todos()[0]
        self.assertIsNone(row["sent_at"])
        self.assertIn("未找到", row["push_error"])

    async def test_exception_and_timeout_release_claim_for_retry(self):
        for error in (RuntimeError("网络错误"), TimeoutError()):
            with self.subTest(error=type(error).__name__):
                with self.db._connection() as conn:
                    conn.execute("UPDATE todos SET retry_at=0")
                self.send.side_effect = error
                await self.service.run_once()
                row = self.db.list_todos()[0]
                self.assertTrue(row["push_error"])
                self.assertIsNone(row["sent_at"])
                self.assertEqual(row["claimed_until"], 0)

    async def test_start_is_idempotent_and_stop_awaits_inflight_send(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def sending(*args):
            entered.set()
            await release.wait()
            return True

        self.send.side_effect = sending
        self.service.start()
        task = self.service._task
        self.service.start()
        self.assertIs(self.service._task, task)
        await asyncio.wait_for(entered.wait(), timeout=5)
        stopping = asyncio.create_task(self.service.stop())
        await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        release.set()
        await asyncio.wait_for(stopping, timeout=5)
        self.assertTrue(self.db.list_todos()[0]["sent_at"])
        self.assertTrue(task.done())


if __name__ == "__main__":
    unittest.main()
