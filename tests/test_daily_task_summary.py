from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ppm.database import Database


class DailyTaskSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "ppm.sqlite3")
        self.db.initialize()
        with self.day("2026-01-01"):
            self.project = self.db.save_project({"name": "日报项目", "status": "进行"})["id"]

    def tearDown(self):
        self.temp.cleanup()

    @contextmanager
    def day(self, value):
        moment = datetime.fromisoformat(value + "T12:00:00+08:00")
        with patch("ppm.database._now", return_value=moment.isoformat()), patch("ppm.task_repository.datetime", wraps=datetime) as clock:
            clock.now.return_value = moment
            yield

    def task(self, **values):
        return self.db.save_task({"project_id": self.project, "name": "接口联调", "start_date": "2026-01-01", **values})

    def log(self, day, **values):
        return self.db.save_work_log({"project_id": self.project, "work_date": day, "content": "当日工作", **values})

    def material(self, day):
        _, text = self.db.team_summary_material([self.project], day, 0)
        return json.loads(next(line for line in text.splitlines() if line.startswith("{")))

    def test_records_group_by_task_id_and_keep_unassigned_work(self):
        with self.day("2026-01-01"):
            a, b = self.task(), self.task()
            self.log("2026-01-01", task_id=a["id"], content="前端完成", task_action="complete")
            self.log("2026-01-01", task_id=b["id"], content="后端完成一部分")
            self.log("2026-01-01", content="项目协调")
        data = self.material("2026-01-01")
        self.assertEqual(data["task_progress_as_of_date"]["progress"], 50)
        self.assertEqual([t["status_as_of_date"] for t in data["tasks"]], ["完成", "未完成"])
        self.assertEqual(data["tasks"][0]["records_on_date"][0]["content"], "前端完成")
        self.assertEqual(data["tasks"][1]["records_on_date"][0]["content"], "后端完成一部分")
        self.assertEqual(data["project_records_on_date"][0]["content"], "项目协调")

    def test_later_rename_reopen_delete_do_not_leak_into_historical_summary(self):
        with self.day("2026-01-01"):
            task = self.task(status="完成", completed_date="2026-01-01")
            self.log("2026-01-01", task_id=task["id"])
        with self.day("2026-01-02"):
            self.task(id=task["id"], name="后来的名字", status="未完成", completed_date=None)
        with self.day("2026-01-03"):
            self.db.delete_task(task["id"], self.project)
        old = self.material("2026-01-01")
        self.assertEqual(old["tasks"][0]["name"], "接口联调")
        self.assertEqual(old["tasks"][0]["status_as_of_date"], "完成")
        self.assertFalse(old["tasks"][0]["deleted_as_of_date"])
        self.assertEqual(old["task_progress_as_of_date"]["progress"], 100)
        reopened = self.material("2026-01-02")
        self.assertEqual(reopened["task_progress_as_of_date"]["progress"], 0)
        self.assertEqual(reopened["tasks"][0]["changes_on_date"][0]["event"], "重新打开")
        self.assertTrue(self.material("2026-01-03")["tasks"][0]["deleted_as_of_date"])

    def test_backfilled_log_has_unknown_state_without_snapshot(self):
        with self.day("2026-01-02"):
            task = self.task()
            self.log("2026-01-01", task_id=task["id"])
        data = self.material("2026-01-01")
        self.assertEqual(data["tasks"][0]["status_as_of_date"], "未知")
        self.assertEqual(data["unknown_task_count"], 1)
        self.assertEqual(data["task_progress_as_of_date"]["task_count"], 0)
        self.assertEqual(len(data["tasks"][0]["records_on_date"]), 1)

    def test_task_only_changes_are_selected_by_default_and_future_tasks_excluded(self):
        with self.day("2026-01-02"):
            task = self.task()
        self.assertEqual(self.db.list_default_summary_project_ids("2026-01-01"), [])
        self.assertEqual(self.db.list_default_summary_project_ids("2026-01-02"), [self.project])
        self.assertEqual(self.db.team_summary_material([], "2026-01-02", 0)[0], [self.project])
        self.assertEqual(self.material("2026-01-01")["tasks"], [])
        with self.day("2026-01-03"):
            self.task(id=task["id"], status="完成", completed_date="2026-01-02")
        data = self.material("2026-01-03")
        self.assertEqual(data["tasks"][0]["completed_date"], "2026-01-02")
        self.assertEqual(data["tasks"][0]["changes_on_date"][0]["changed_at"][:10], "2026-01-03")
        self.assertEqual(data["tasks"][0]["records_on_date"], [])


if __name__ == "__main__":
    unittest.main()
