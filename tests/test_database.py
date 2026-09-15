import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from ppm.database import Database
from ppm.errors import ValidationError


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

    def test_project_id_is_numeric_and_immutable(self):
        project = self.db.save_project({"name": "编号项目"})
        self.assertIsInstance(project["id"], int)

        updated = self.db.save_project(
            {"id": project["id"], "name": "编号项目已更新", "status": "准备"}
        )

        self.assertEqual(updated["id"], project["id"])
        self.assertIsInstance(updated["id"], int)

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

    def test_project_lists_expose_latest_record_date_without_content(self):
        project = self.db.save_project({"name": "最新记录项目"})
        self.db.save_work_log(
            {"project_id": project["id"], "work_date": "2026-01-02", "content": "较早记录"}
        )
        self.db.save_work_log(
            {"project_id": project["id"], "work_date": "2026-01-05", "content": "最新记录内容"}
        )

        listed = next(item for item in self.db.list_projects() if item["id"] == project["id"])
        dashboard = next(
            item for item in self.db.dashboard()["recent_projects"] if item["id"] == project["id"]
        )

        self.assertEqual(listed["latest_record_date"], "2026-01-05")
        self.assertNotIn("latest_log", listed)
        self.assertEqual(dashboard["latest_record_date"], "2026-01-05")
        self.assertNotIn("latest", dashboard)

    def test_project_overview_defaults_to_non_finished_and_filters_statuses(self):
        ready = self.db.save_project({"name": "准备项目", "status": "准备"})
        active = self.db.save_project({"name": "进行项目", "status": "进行"})
        maintenance = self.db.save_project({"name": "维护项目", "status": "维护"})
        paused = self.db.save_project({"name": "暂停项目", "status": "暂停"})
        finished = self.db.save_project({"name": "结束项目", "status": "结束"})

        default_projects = self.db.list_project_overview()
        selected_projects = self.db.list_project_overview(["准备", "暂停"])

        self.assertEqual(
            [item["id"] for item in default_projects],
            [ready["id"], active["id"], maintenance["id"], paused["id"]],
        )
        self.assertEqual(
            [item["id"] for item in selected_projects], [ready["id"], paused["id"]]
        )
        self.assertEqual(
            [item["id"] for item in self.db.list_project_overview(["结束"])],
            [finished["id"]],
        )
        self.assertEqual(
            set(selected_projects[0]), {"id", "name", "status"}
        )
        with self.assertRaisesRegex(ValidationError, "正确写法：/项目总览"):
            self.db.list_project_overview(["执行中"])

    def test_default_summary_projects_match_advancing_statuses(self):
        ready = self.db.save_project({"name": "准备总结", "status": "准备"})
        active = self.db.save_project({"name": "进行总结", "status": "进行"})
        maintenance = self.db.save_project({"name": "维护总结", "status": "维护"})
        self.db.save_project({"name": "暂停总结", "status": "暂停"})
        self.db.save_project({"name": "结束总结", "status": "结束"})

        self.assertEqual(
            self.db.list_default_summary_project_ids(),
            [ready["id"], active["id"], maintenance["id"]],
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

    def test_calendar_override_accepts_future_date(self):
        future = datetime.now().astimezone().date() + timedelta(days=30)
        self.db.save_calendar_day(
            {"date": future.isoformat(), "is_workday": False, "note": "提前安排休班"}
        )

        calendar = self.db.get_calendar(future.strftime("%Y-%m"))
        day = next(item for item in calendar["days"] if item["date"] == future.isoformat())
        self.assertFalse(day["is_workday"])
        self.assertEqual(day["source"], "人工调整")

    def test_dashboard_attendance_matches_monthly_summary_until_today(self):
        member = self.db.save_member({"name": "仪表盘成员"})
        today = datetime.now().astimezone().date()
        self.db.save_attendance(
            {"member_id": member["id"], "date": today.isoformat(), "status": "加班"}
        )
        self.db.save_attendance(
            {
                "member_id": member["id"],
                "date": (today + timedelta(days=1)).isoformat(),
                "status": "调休",
            }
        )

        month_start = today.replace(day=1).isoformat()
        monthly = self.db.attendance_summary(month_start, today.isoformat())["summary"]
        expected = {
            "normal": sum(row["normal_days"] for row in monthly),
            "overtime": sum(row["overtime_days"] for row in monthly),
            "time_off": sum(row["time_off_days"] for row in monthly),
        }

        self.assertEqual(self.db.dashboard()["attendance"], expected)

    def test_attendance_only_marks_advancing_projects(self):
        member = self.db.save_member({"name": "项目成员"})
        for status in ("准备", "进行", "维护", "暂停", "结束"):
            self.db.save_project(
                {
                    "name": f"{status}项目",
                    "status": status,
                    "member_ids": [member["id"]],
                }
            )
        day = datetime.now().astimezone().date().isoformat()

        assignments = self.db.attendance_matrix(day[:7])["assignments"]

        self.assertEqual(
            set(assignments[f"{member['id']}:{day}"]),
            {"准备项目", "进行项目", "维护项目"},
        )

    def test_attendance_uses_project_status_effective_on_each_date(self):
        member = self.db.save_member({"name": "状态成员"})
        project = self.db.save_project({"name": "阶段项目", "status": "进行"})
        today = datetime.now().astimezone().date()
        active_date = today.replace(day=1)
        paused_date = active_date + timedelta(days=1)

        initial = self.db.project_detail(project["id"])["status_history"][0]
        self.db.update_status_history(
            {
                "id": initial["id"],
                "project_id": project["id"],
                "from_status": None,
                "to_status": "进行",
                "changed_at": f"{active_date.isoformat()}T00:00:00+08:00",
                "note": "项目启动",
            }
        )
        self.db.insert_membership_history(
            {
                "project_id": project["id"],
                "member_id": member["id"],
                "joined_at": active_date.isoformat(),
            }
        )
        self.db.insert_status_history(
            {
                "project_id": project["id"],
                "from_status": "进行",
                "to_status": "暂停",
                "changed_at": f"{paused_date.isoformat()}T00:00:00+08:00",
                "note": "暂停推进",
            }
        )

        assignments = self.db.attendance_matrix(active_date.strftime("%Y-%m"))["assignments"]

        self.assertIn(
            "阶段项目", assignments.get(f"{member['id']}:{active_date}", [])
        )
        self.assertNotIn(
            "阶段项目", assignments.get(f"{member['id']}:{paused_date}", [])
        )

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

    def test_team_summary_keeps_one_record_per_day_and_overwrites_explicitly(self):
        project = self.db.save_project({"name": "日报项目", "status": "进行"})
        day = datetime.now().astimezone().date().isoformat()

        self.assertTrue(
            self.db.save_team_summary([project["id"]], day, "model-a", "第一版")
        )
        self.assertFalse(
            self.db.save_team_summary([project["id"]], day, "model-b", "预览版")
        )
        self.assertEqual(self.db.get_team_summary(day)["content"], "第一版")

        self.assertTrue(
            self.db.save_team_summary(
                [project["id"]], day, "model-b", "覆盖版", overwrite=True
            )
        )
        self.assertEqual(self.db.get_team_summary(day)["content"], "覆盖版")
        updated = self.db.update_team_summary_content(day, "人工修订版")
        self.assertEqual(updated["content"], "人工修订版")
        self.assertEqual(updated["project_names"], ["日报项目"])
        self.db.delete_team_summary(day)
        self.assertIsNone(self.db.get_team_summary(day))

    def test_team_summary_can_include_previous_calendar_days(self):
        project = self.db.save_project({"name": "连续日报", "status": "进行"})
        today = datetime.now().astimezone().date()
        previous = (today - timedelta(days=1)).isoformat()
        self.db.save_team_summary([project["id"]], previous, "model", "昨日总结")

        _, with_history = self.db.team_summary_material(
            [project["id"]], today.isoformat(), 3
        )
        _, without_history = self.db.team_summary_material(
            [project["id"]], today.isoformat(), 0
        )

        self.assertIn("昨日总结", with_history)
        self.assertNotIn("昨日总结", without_history)

        listed = self.db.list_team_summaries(previous, today.isoformat())
        self.assertEqual(listed["dates"], [previous, today.isoformat()])
        self.assertEqual(listed["summaries"][0]["summary_date"], previous)
        self.assertEqual(listed["summaries"][0]["project_names"], ["连续日报"])

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

    def test_work_log_preserves_internal_spacing_and_newlines(self):
        project = self.db.save_project({"name": "格式记录"})
        content = "第一行  连续空格\n  第二行缩进\n第三行"

        saved = self.db.save_work_log(
            {
                "project_id": project["id"],
                "work_date": datetime.now().astimezone().date().isoformat(),
                "content": content,
            }
        )

        self.assertEqual(saved["content"], content)

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
