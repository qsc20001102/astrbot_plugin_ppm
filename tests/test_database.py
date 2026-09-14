import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from ppm.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "ppm.sqlite3")
        self.db.initialize()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_project_history_and_membership_are_traceable(self):
        member = self.db.save_member({"name": "张三", "role": "开发"})
        project = self.db.save_project(
            {"name": "测试项目", "status": "准备", "member_ids": [member["id"]]}
        )
        self.db.save_project(
            {
                "id": project["id"],
                "name": "测试项目",
                "status": "进行",
                "member_ids": [],
                "status_note": "评审通过",
            }
        )

        detail = self.db.project_detail(project["id"])
        self.assertEqual(detail["status"], "进行")
        self.assertEqual(len(detail["status_history"]), 2)
        self.assertEqual(detail["status_history"][0]["note"], "评审通过")
        self.assertIsNotNone(detail["membership_history"][0]["left_at"])

    def test_work_log_appears_in_timeline(self):
        project = self.db.save_project({"name": "进程项目"})
        day = datetime.now().astimezone().date().isoformat()
        self.db.save_work_log(
            {
                "project_id": project["id"],
                "work_date": day,
                "content": "完成接口设计",
            }
        )

        timeline = self.db.timeline(day, day)
        self.assertEqual(
            timeline["logs"][f"{project['id']}:{day}"][0]["content"], "完成接口设计"
        )

    def test_attendance_defaults_to_normal_and_override_is_counted(self):
        member = self.db.save_member({"name": "王五"})
        current = datetime.now().astimezone().date()
        while current.weekday() >= 5:
            current -= timedelta(days=1)
        day = current.isoformat()
        self.db.save_calendar_day(
            {"date": day, "is_workday": True, "note": "测试工作日"}
        )

        before = self.db.attendance_summary(day, day)["summary"][0]
        self.assertEqual(before["normal_days"], 1)
        self.db.save_attendance(
            {"member_id": member["id"], "date": day, "status": "调休"}
        )
        after = self.db.attendance_summary(day, day)["summary"][0]
        self.assertEqual(after["normal_days"], 0)
        self.assertEqual(after["time_off_days"], 1)
        record = self.db.attendance_matrix(day[:7])["records"][f"{member['id']}:{day}"]
        self.assertEqual(record["hours"], 8)

    def test_soft_delete_keeps_project_history(self):
        project = self.db.save_project({"name": "归档项目", "status": "结束"})
        self.db.delete_project(project["id"])
        self.assertEqual(self.db.list_projects(), [])

    def test_history_can_be_edited_and_recalculates_current_status(self):
        member = self.db.save_member({"name": "赵六"})
        project = self.db.save_project(
            {"name": "历史修正", "status": "准备", "member_ids": [member["id"]]}
        )
        detail = self.db.project_detail(project["id"])
        status_history = detail["status_history"][0]
        self.db.update_status_history(
            {
                "id": status_history["id"],
                "project_id": project["id"],
                "from_status": "准备",
                "to_status": "维护",
                "changed_at": datetime.now().astimezone().isoformat(),
                "note": "修正记录",
            }
        )
        membership = detail["membership_history"][0]
        self.db.update_membership_history(
            {
                "id": membership["id"],
                "project_id": project["id"],
                "joined_at": membership["joined_at"],
                "left_at": membership["joined_at"],
                "join_reason": "补录",
                "leave_reason": "临时退出",
            }
        )

        updated = self.db.project_detail(project["id"])
        self.assertEqual(updated["status"], "维护")
        self.assertEqual(updated["status_history"][0]["note"], "修正记录")
        self.assertEqual(updated["membership_history"][0]["leave_reason"], "临时退出")

    def test_team_summary_combines_selected_projects(self):
        first = self.db.save_project({"name": "项目甲", "status": "进行"})
        second = self.db.save_project({"name": "项目乙", "status": "维护"})
        day = datetime.now().astimezone().date().isoformat()
        self.db.save_work_log(
            {"project_id": first["id"], "work_date": day, "content": "完成甲任务"}
        )

        project_ids, material = self.db.team_summary_material(
            [first["id"], second["id"]], day
        )
        self.assertEqual(set(project_ids), {first["id"], second["id"]})
        self.assertIn("完成甲任务", material)
        self.assertIn("项目乙", material)

    def test_work_logs_can_be_listed_edited_and_deleted(self):
        project = self.db.save_project({"name": "日志维护"})
        day = datetime.now().astimezone().date().isoformat()
        log = self.db.save_work_log(
            {"project_id": project["id"], "work_date": day, "content": "初始内容"}
        )
        self.db.save_work_log(
            {
                "id": log["id"],
                "project_id": project["id"],
                "work_date": day,
                "content": "修正内容",
            }
        )
        listed = self.db.list_work_logs(project["id"], day)
        self.assertEqual(listed["project"]["name"], "日志维护")
        self.assertEqual(listed["logs"][0]["content"], "修正内容")
        self.db.delete_work_log(log["id"])
        self.assertEqual(self.db.list_work_logs(project["id"], day)["logs"], [])

    def test_status_history_can_be_inserted_and_deleted(self):
        project = self.db.save_project({"name": "状态维护", "status": "准备"})
        initial = self.db.project_detail(project["id"])["status_history"][0]
        self.db.insert_status_history(
            {
                "project_id": project["id"],
                "from_status": "准备",
                "to_status": "进行",
                "changed_at": datetime.now().astimezone().isoformat(),
                "note": "手动补录",
            }
        )
        detail = self.db.project_detail(project["id"])
        self.assertEqual(detail["status"], "进行")
        inserted = next(item for item in detail["status_history"] if item["note"] == "手动补录")
        self.db.delete_status_history(inserted["id"], project["id"])
        self.assertEqual(self.db.project_detail(project["id"])["status"], "准备")
        self.assertEqual(self.db.project_detail(project["id"])["status_history"][0]["id"], initial["id"])

    def test_membership_history_can_be_inserted_and_deleted(self):
        member = self.db.save_member({"name": "补录成员"})
        project = self.db.save_project({"name": "成员维护"})
        day = datetime.now().astimezone().date().isoformat()
        self.db.insert_membership_history(
            {
                "project_id": project["id"],
                "member_id": member["id"],
                "joined_at": day,
                "join_reason": "手动补录",
            }
        )
        membership = self.db.project_detail(project["id"])["membership_history"][0]
        self.assertEqual(membership["name"], "补录成员")
        self.db.delete_membership_history(membership["id"], project["id"])
        self.assertEqual(self.db.project_detail(project["id"])["membership_history"], [])


if __name__ == "__main__":
    unittest.main()
