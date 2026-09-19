import json
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

    def test_invalid_input_does_not_create_entities(self):
        for value in (None, [], {}, True, 123):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.db.save_member({"name": value})
        for value in (False, 0, 1.5, "1.5", 2**64):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.db.save_member({"id": value, "name": "invalid"})
        for value in ("12", {}, 1, None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.db.save_project({"name": "invalid", "member_ids": value})
        self.assertEqual(self.db.list_members(), [])
        self.assertEqual(self.db.list_projects(), [])

    def test_duplicate_member_name_is_validation_error(self):
        member = self.db.save_member({"name": "同名"})
        with self.assertRaises(ValidationError):
            self.db.save_member({"name": "同名"})
        self.db.save_member({"id": member["id"], "name": "同名"})
        self.db.delete_member(member["id"])
        self.db.save_member({"name": "同名"})

    def test_delete_preserves_closed_membership_reasons(self):
        for delete_member in (True, False):
            member = self.db.save_member({"name": str(delete_member)})
            project = self.db.save_project({"name": "历史", "member_ids": [member["id"]]})
            history = self.db.project_detail(project["id"])["membership_history"][0]
            self.db.update_membership_history({"id": history["id"], "project_id": project["id"],
                "joined_at": history["joined_at"], "left_at": history["joined_at"], "leave_reason": "原始原因"})
            if delete_member:
                self.db.delete_member(member["id"])
            else:
                self.db.delete_project(project["id"])
            with self.db._connection() as conn:
                row = conn.execute("SELECT * FROM project_memberships WHERE id=?", (history["id"],)).fetchone()
            self.assertEqual(row["leave_reason"], "原始原因")
            self.assertEqual(row["left_at"], history["joined_at"])

    def test_invalid_hours_are_rejected_before_writing(self):
        member = self.db.save_member({"name": "工时"})
        for value in ("abc", [], {}, True, "nan", "inf", -1, 25):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.db.save_attendance({"member_id": member["id"], "date": "2026-01-01", "hours": value})
        with self.db._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0], 0)

    def test_attendance_details_exclude_deleted_members(self):
        member = self.db.save_member({"name": "离职"})
        self.db.save_attendance({"member_id": member["id"], "date": "2026-01-01", "status": "加班"})
        self.db.delete_member(member["id"])
        result = self.db.attendance_summary("2026-01-01", "2026-01-02")
        self.assertEqual(result["summary"], [])
        self.assertEqual(result["details"], [])

    def test_timeline_rejects_reverse_range_and_excludes_deleted_logs(self):
        with self.assertRaises(ValidationError):
            self.db.timeline("2026-02-01", "2026-01-01")
        project = self.db.save_project({"name": "删除日志"})
        self.db.save_work_log({"project_id": project["id"], "work_date": "2026-01-01", "content": "保留"})
        self.db.delete_project(project["id"])
        self.assertEqual(self.db.timeline("2026-01-01", "2026-01-01")["logs"], {})
        with self.db._connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM work_logs").fetchone()[0], 1)

    def test_summary_rejects_missing_content_and_invalid_selection(self):
        for content in (None, {}, [], " ", "x" * 20001):
            with self.subTest(content_type=type(content)), self.assertRaises(ValidationError):
                self.db.save_team_summary([], "2026-01-01", "model", content)
        for ids in ("12", {}, False, 12):
            with self.subTest(ids=ids), self.assertRaises(ValidationError):
                self.db.team_summary_material(ids, "2026-01-01")
        for days in (True, 1.2, "nan"):
            with self.subTest(days=days), self.assertRaises(ValidationError):
                self.db.team_summary_material([], "2026-01-01", days)
        self.assertIsNone(self.db.get_team_summary("2026-01-01"))

    def test_calendar_max_date_and_invalid_month(self):
        self.assertEqual(len(self.db.get_calendar("9999-12")["days"]), 31)
        for month in (None, [], "2026-1", "2026-13"):
            with self.subTest(month=month), self.assertRaises(ValidationError):
                self.db.get_calendar(month)

    def test_empty_dashboard_has_numeric_metrics(self):
        self.assertEqual(self.db.dashboard()["metrics"], {"total": 0, "active": 0, "finished": 0, "paused": 0})

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

    def test_editing_does_not_reorder_projects_members_or_histories(self):
        first_member = self.db.save_member({"name": "Z"})
        second_member = self.db.save_member({"name": "A"})
        first = self.db.save_project({"name": "首个项目", "member_ids": [first_member["id"], second_member["id"]]})
        second = self.db.save_project({"name": "第二项目", "member_ids": [first_member["id"]]})
        self.db.insert_status_history({"project_id": first["id"], "to_status": "维护",
                                       "changed_at": "2020-01-01T00:00:00"})
        before = self.db.project_detail(first["id"])
        ids = lambda rows: [row["id"] for row in rows]
        recent_ids = ids(self.db.dashboard()["recent_projects"])
        member_project_ids = ids(self.db.list_members()[0]["projects"])
        self.db.save_member({"id": first_member["id"], "name": "0"})
        self.db.save_project({"id": first["id"], "name": "改名项目", "status": before["status"],
                              "member_ids": [first_member["id"], second_member["id"]]})
        history = before["status_history"][0]
        self.db.update_status_history({"id": history["id"], "project_id": first["id"],
                                       "to_status": "维护", "changed_at": "2099-01-01T00:00:00"})
        self.assertEqual(ids(self.db.list_projects()), [first["id"], second["id"]])
        self.assertEqual(ids(self.db.list_members()), [first_member["id"], second_member["id"]])
        self.assertEqual(ids(self.db.dashboard()["recent_projects"]), recent_ids)
        self.assertEqual(ids(self.db.list_members()[0]["projects"]), member_project_ids)
        self.assertEqual(ids(self.db.project_detail(first["id"])["status_history"]),
                         ids(before["status_history"]))
        self.assertEqual(self.db.project_detail(first["id"])["status"], "维护")

    def test_lifecycle_material_contains_all_logs_and_chronological_stages(self):
        project = self.db.save_project({"name": "全周期项目", "status": "维护"})
        other = self.db.save_project({"name": "其他项目"})
        self.db.insert_status_history({"project_id": project["id"], "to_status": "进行",
                                       "changed_at": "2020-01-01T00:00:00"})
        for index in range(65):
            self.db.save_work_log({"project_id": project["id"], "work_date": "2020-01-02",
                                   "content": f"工作记录{index}"})
        self.db.save_work_log({"project_id": other["id"], "work_date": "2020-01-02", "content": "不应混入"})
        material = json.loads(self.db.project_lifecycle_material(project["id"]))
        self.assertEqual(len(material["work_logs"]), 65)
        self.assertEqual(material["work_logs"][0]["content"], "工作记录0")
        self.assertEqual(material["work_logs"][-1]["content"], "工作记录64")
        self.assertEqual(material["work_logs"][0]["status_on_date"], "进行")
        self.assertEqual(material["status_history"][0]["to_status"], "进行")
        self.assertEqual(material["project"]["id"], project["id"])

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

    def test_default_summary_projects_require_records_regardless_of_status(self):
        day = datetime.now().astimezone().date().isoformat()
        expected = []
        for status in ("准备", "进行", "维护", "暂停", "结束"):
            project = self.db.save_project({"name": status, "status": status})
            self.db.save_project({"name": status + "无记录", "status": status})
            self.db.save_work_log({"project_id": project["id"], "work_date": day, "content": "当日工作"})
            expected.append(project["id"])
        self.assertEqual(self.db.list_default_summary_project_ids(day), expected)
        self.assertEqual(self.db.team_summary_material([], day)[0], expected)

    def test_summary_uses_selected_date_status_and_excludes_uncreated_projects(self):
        project = self.db.save_project({"name": "历史总结", "status": "结束"})
        with self.db._connection() as conn:
            conn.execute("DELETE FROM project_status_history WHERE project_id=?", (project["id"],))
        for day, status in [("2026-01-02", "准备"), ("2026-01-03", "进行"),
                            ("2026-01-04", "暂停"), ("2026-01-05", "维护"),
                            ("2026-01-06", "结束")]:
            self.db.insert_status_history({"project_id": project["id"],
                                           "to_status": status, "changed_at": day + "T12:00:00"})
        self.assertEqual(self.db.summary_projects("2026-01-01"), [])
        for day, status, selected in [("2026-01-03", "进行", True),
                                      ("2026-01-04", "暂停", False),
                                      ("2026-01-05", "维护", True),
                                      ("2026-01-06", "结束", False)]:
            with self.subTest(day=day):
                if selected:
                    self.db.save_work_log({"project_id": project["id"], "work_date": day, "content": "历史工作"})
                self.assertEqual(self.db.summary_projects(day)[0]["status"], status)
                self.assertEqual(self.db.list_default_summary_project_ids(day),
                                 [project["id"]] if selected else [])
                _, material = self.db.team_summary_material([project["id"]], day, 0)
                self.assertIn(f"状态：{status}", material)
        ids, _ = self.db.team_summary_material([], "2026-01-03", 0)
        self.assertEqual(ids, [project["id"]])
        with self.assertRaises(ValidationError):
            self.db.team_summary_material([], "2026-01-04", 0)
        with self.assertRaises(ValidationError):
            self.db.team_summary_material([project["id"]], "2026-01-01", 0)

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
            {"进行项目"},
        )

    def test_maintenance_attendance_requires_project_log_on_that_date(self):
        members = [self.db.save_member({"name": name}) for name in ("甲", "乙")]
        project = self.db.save_project({"name": "维护项目", "status": "维护",
                                        "member_ids": [m["id"] for m in members]})
        day = datetime.now().astimezone().date().isoformat()
        other = self.db.save_project({"name": "其他项目", "status": "维护"})
        self.db.save_work_log({"project_id": other["id"], "work_date": day, "content": "其他项目记录"})
        self.db.save_work_log({"project_id": project["id"],
                               "work_date": (datetime.fromisoformat(day) - timedelta(days=1)).date().isoformat(),
                               "content": "昨日记录"})
        self.assertEqual(self.db.attendance_matrix(day[:7])["assignments"], {})
        log = self.db.save_work_log({"project_id": project["id"], "work_date": day, "content": "今日维护"})
        assignments = self.db.attendance_matrix(day[:7])["assignments"]
        for member in members:
            self.assertEqual(assignments[f"{member['id']}:{day}"], ["维护项目"])
        self.db.delete_work_log(log["id"])
        self.assertEqual(self.db.attendance_matrix(day[:7])["assignments"], {})

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
