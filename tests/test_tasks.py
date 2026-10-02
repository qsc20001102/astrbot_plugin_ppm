import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from ppm.database import Database
from ppm.errors import NotFoundError, ValidationError


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ppm.sqlite3"
        self.db = Database(self.path)
        self.db.initialize()
        self.project = self.db.save_project({"name": "任务项目"})["id"]

    def tearDown(self):
        self.temp.cleanup()

    def task(self, **values):
        return self.db.save_task({"project_id": self.project, "name": "需求", "start_date": "2026-01-01", **values})

    def log(self, **values):
        return self.db.save_work_log({"project_id": self.project, "work_date": "2026-01-03", "content": "完成评审", **values})

    def test_progress_and_no_tasks(self):
        self.assertEqual(self.db.project_detail(self.project)["progress"], 0)
        self.task(status="完成", completed_date="2026-01-02")
        self.task()
        self.task()
        for project in (self.db.project_detail(self.project), self.db.list_projects()[0]):
            self.assertEqual((project["task_count"], project["completed_task_count"], project["progress"]), (3, 1, 33))

    def test_progress_rounds_half_up_consistently_with_web(self):
        self.task(status="完成", completed_date="2026-01-02")
        for _ in range(7):
            self.task()
        self.assertEqual(self.db.project_detail(self.project)["progress"], 13)
        self.assertEqual(self.db.list_projects()[0]["progress"], 13)

    def test_complete_reopen_and_date_correction_preserve_history(self):
        task = self.task()
        self.task(id=task["id"], status="完成", completed_date="2026-01-05")
        self.task(id=task["id"], status="完成", completed_date="2026-01-06")
        self.db.save_task({"id": task["id"], "project_id": self.project, "status": "未完成"})
        current = self.db.list_tasks(self.project)[0]
        self.assertIsNone(current["completed_date"])
        self.assertEqual([h["event"] for h in current["history"]], ["创建", "完成", "修改", "重新打开"])
        self.assertEqual(current["history"][1]["snapshot"]["completed_date"], "2026-01-05")

    def test_log_changes_status_atomically_and_legacy_edits_keep_link(self):
        task = self.task()
        log = self.log(task_id=task["id"], task_action="complete")
        self.assertEqual(self.db.list_tasks(self.project)[0]["completed_date"], "2026-01-03")
        self.assertEqual(self.log(id=log["id"], content="修正文字")["task_id"], task["id"])
        self.log(id=log["id"], task_id=task["id"], task_action="reopen")
        self.assertEqual(self.db.list_tasks(self.project)[0]["status"], "未完成")
        self.assertEqual(self.log(id=log["id"], task_id=None)["task_id"], None)

    def test_invalid_records_do_not_complete_task(self):
        task = self.task()
        for values in ({"content": ""}, {"id": 999}, {"work_date": "2025-12-31"}):
            with self.subTest(values=values), self.assertRaises((ValidationError, NotFoundError)):
                self.log(task_id=task["id"], task_action="complete", **values)
        self.assertEqual(self.db.list_tasks(self.project)[0]["status"], "未完成")
        self.assertEqual(self.db.project_detail(self.project)["work_logs"], [])
        self.assertEqual(len(self.db.list_tasks(self.project)[0]["history"]), 1)

    def test_sql_failure_rolls_back_task_state_and_audit(self):
        task = self.task()
        with self.db._connection() as conn:
            conn.execute("CREATE TRIGGER fail_log BEFORE INSERT ON work_logs BEGIN SELECT RAISE(ABORT, 'simulated failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.log(task_id=task["id"], task_action="complete")
        current = self.db.list_tasks(self.project)[0]
        self.assertEqual(current["status"], "未完成")
        self.assertEqual(len(current["history"]), 1)
        self.assertEqual(self.db.project_detail(self.project)["work_logs"], [])

    def test_cross_project_and_invalid_task_ids_rejected(self):
        other = self.db.save_project({"name": "其他项目"})["id"]
        task = self.task(project_id=other)
        for values in ({"task_id": task["id"]}, {"task_id": True}, {"task_id": 999}, {"task_action": "complete"}, {"task_action": "wrong"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.log(**values)
        with self.assertRaises(NotFoundError):
            self.task(id=task["id"])
        with self.assertRaises(NotFoundError):
            self.db.delete_task(task["id"], self.project)

    def test_delete_keeps_records_and_audit_but_excludes_progress(self):
        task = self.task()
        log = self.log(task_id=task["id"])
        self.db.delete_task(task["id"], self.project)
        detail = self.db.project_detail(self.project)
        self.assertEqual(detail["task_count"], 0)
        self.assertEqual(detail["work_logs"][0]["task_name"], "需求")
        self.assertTrue(detail["work_logs"][0]["task_deleted_at"])
        self.log(id=log["id"], content="保留关联的修正")
        with self.assertRaises(ValidationError):
            self.log(task_id=task["id"])
        with self.assertRaises(ValidationError):
            self.log(id=log["id"], task_action="complete")
        self.assertEqual(self.db.list_tasks(self.project)[0]["history"][-1]["event"], "删除")

    def test_delete_log_does_not_undo_completion(self):
        task = self.task()
        log = self.log(task_id=task["id"], task_action="complete")
        self.db.delete_work_log(log["id"])
        self.assertEqual(self.db.list_tasks(self.project)[0]["status"], "完成")

    def test_validation_and_duration(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for values in ({"status":"doing"}, {"name":" "}, {"start_date":"bad"}, {"status":"完成","completed_date":"2025-01-01"}, {"status":"完成","completed_date":tomorrow}, {"completed_date":"2026-01-02"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.task(**values)
        self.task(status="完成",completed_date="2026-01-01")
        self.task(start_date=tomorrow)
        tasks = self.db.list_tasks(self.project)
        self.assertEqual([t["duration_days"] for t in tasks], [1, 0])

    def test_deleted_project_cannot_mutate_tasks(self):
        task = self.task()
        self.db.delete_project(self.project)
        with self.assertRaises(NotFoundError):
            self.task(id=task["id"])
        with self.assertRaises(NotFoundError):
            self.db.delete_task(task["id"], self.project)

    def test_lifecycle_uses_historical_status_and_task_specific_logs(self):
        task = self.task(status="完成", completed_date="2026-01-10")
        other = self.task(name="其他任务")
        with self.db._connection() as conn:
            conn.execute("DELETE FROM project_status_history WHERE project_id=?", (self.project,))
        for day, status in (("01", "进行"), ("03", "维护"), ("06", "暂停"), ("08", "进行")):
            self.db.insert_status_history({"project_id": self.project, "to_status": status,
                                           "changed_at": f"2026-01-{day}T09:00:00"})
        record = self.log(task_id=task["id"], work_date="2026-01-04")
        duplicate = self.log(task_id=task["id"], work_date="2026-01-04")
        self.log(task_id=other["id"], work_date="2026-01-03")
        self.log(work_date="2026-01-05")
        self.log(task_id=task["id"], work_date="2026-01-06")
        self.log(task_id=task["id"], work_date="2026-01-11")
        current = self.db.project_detail(self.project)["tasks"][0]
        self.assertEqual(current["duration_days"], 6)
        self.assertEqual(current["active_periods"], [
            {"start_date": "2026-01-01", "end_date": "2026-01-02"},
            {"start_date": "2026-01-04", "end_date": "2026-01-04"},
            {"start_date": "2026-01-08", "end_date": "2026-01-10"},
        ])
        self.db.delete_work_log(duplicate["id"])
        self.log(id=record["id"], task_id=None, work_date="2026-01-04")
        self.assertEqual(self.db.list_tasks(self.project)[0]["duration_days"], 5)

    def test_lifecycle_same_day_history_corrections_and_empty_periods(self):
        self.task(status="完成", completed_date="2026-01-02")
        for status in ("进行", "维护", "暂停"):
            self.db.insert_status_history({"project_id": self.project, "to_status": status,
                                           "changed_at": "2026-01-01T09:00:00"})
        current = self.db.list_tasks(self.project)[0]
        self.assertEqual(current["duration_days"], 0)
        self.assertEqual(current["active_periods"], [])
        history = next(h for h in self.db.project_detail(self.project)["status_history"]
                       if h["to_status"] == "暂停")
        self.db.update_status_history({**history, "to_status": "进行"})
        self.assertEqual(self.db.list_tasks(self.project)[0]["duration_days"], 2)

    def test_open_and_deleted_lifecycle_stop_at_their_end_date(self):
        today = date.today()
        start = (today - timedelta(days=2)).isoformat()
        task = self.task(start_date=start)
        self.assertEqual(self.db.list_tasks(self.project)[0]["duration_days"], 3)
        self.db.delete_task(task["id"], self.project)
        with self.db._connection() as conn:
            conn.execute("UPDATE project_tasks SET deleted_at=? WHERE id=?", (start + "T12:00:00", task["id"]))
        self.assertEqual(self.db.list_tasks(self.project)[0]["active_periods"],
                         [{"start_date": start, "end_date": start}])

    def test_daily_reads_include_task_links(self):
        task = self.task()
        self.log(task_id=task["id"])
        self.assertEqual(self.db.list_work_logs(self.project,"2026-01-03")["logs"][0]["task_name"],"需求")
        self.assertEqual(self.db.timeline("2026-01-03","2026-01-03")["logs"][f"{self.project}:2026-01-03"][0]["task_id"],task["id"])

    def test_migration_preserves_old_records_and_is_repeatable(self):
        self.log()
        with self.db._connection() as conn:
            conn.execute("DROP INDEX idx_work_logs_task")
            conn.execute("ALTER TABLE work_logs DROP COLUMN task_id")
            conn.execute("PRAGMA user_version=2")
        for _ in range(2):
            db = Database(self.path)
            db.initialize()
            logs = db.project_detail(self.project)["work_logs"]
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0]["content"], "完成评审")
            self.assertIsNone(logs[0]["task_id"])

    def test_repeat_status_save_does_not_duplicate_history(self):
        task = self.task()
        self.db.save_task({"id":task["id"],"project_id":self.project,"status":"未完成"})
        self.log(task_id=task["id"],task_action="complete")
        self.log(task_id=task["id"],task_action="complete")
        self.assertEqual(len(self.db.list_tasks(self.project)[0]["history"]), 2)


if __name__ == "__main__":
    unittest.main()
