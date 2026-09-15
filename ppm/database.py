from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .calendar_service import date_range, default_is_workday, month_bounds
from .errors import NotFoundError, ValidationError
from .validation import (
    optional_text,
    parse_date,
    parse_datetime,
    parse_id,
    required_text,
)

PROJECT_STATUS_ORDER = ("准备", "进行", "维护", "暂停", "结束")
PROJECT_STATUSES = set(PROJECT_STATUS_ORDER)
DEFAULT_PROJECT_OVERVIEW_STATUSES = PROJECT_STATUS_ORDER[:-1]
ADVANCING_PROJECT_STATUSES = {"准备", "进行", "维护"}
ATTENDANCE_STATUSES = {"正常", "加班", "调休"}


class Database:
    """SQLite repository and application service for PPM.

    A connection is created per operation so Web API requests can safely run on
    different threads. Mutations use BEGIN IMMEDIATE to keep history and current
    state changes atomic.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            with self._connection() as conn:
                conn.executescript(SCHEMA)
                conn.execute("PRAGMA user_version = 1")
            self._initialized = True

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _rows(rows) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def dashboard(self) -> dict[str, Any]:
        today = _today().isoformat()
        with self._connection() as conn:
            metrics = dict(
                conn.execute(
                    """SELECT COUNT(*) total,
                SUM(status='进行') active, SUM(status='结束') finished,
                SUM(status='暂停') paused FROM projects WHERE deleted_at IS NULL"""
                ).fetchone()
            )
            recent = self._rows(
                conn.execute(
                    """SELECT p.id,p.name,p.status,p.updated_at,
                (SELECT work_date FROM work_logs w WHERE w.project_id=p.id
                  ORDER BY work_date DESC,id DESC LIMIT 1) latest_record_date
                FROM projects p WHERE p.deleted_at IS NULL
                ORDER BY p.updated_at DESC LIMIT 6"""
                )
            )
        month_start = _today().replace(day=1).isoformat()
        attendance_rows = self.attendance_summary(month_start, today)["summary"]
        attendance = {
            "normal": sum(row["normal_days"] for row in attendance_rows),
            "overtime": sum(row["overtime_days"] for row in attendance_rows),
            "time_off": sum(row["time_off_days"] for row in attendance_rows),
        }
        return {"metrics": metrics, "recent_projects": recent, "attendance": attendance}

    def list_members(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = self._rows(
                conn.execute(
                    """SELECT m.*,
                COUNT(DISTINCT CASE WHEN pm.left_at IS NULL AND p.status='准备' THEN p.id END) ready_projects,
                COUNT(DISTINCT CASE WHEN pm.left_at IS NULL AND p.status='进行' THEN p.id END) active_projects,
                COUNT(DISTINCT CASE WHEN pm.left_at IS NULL AND p.status='维护' THEN p.id END) maintenance_projects,
                COUNT(DISTINCT CASE WHEN pm.left_at IS NULL AND p.status='暂停' THEN p.id END) paused_projects,
                COUNT(DISTINCT CASE WHEN pm.left_at IS NULL AND p.status='结束' THEN p.id END) completed_projects
                FROM members m
                LEFT JOIN project_memberships pm ON pm.member_id=m.id
                LEFT JOIN projects p ON p.id=pm.project_id AND p.deleted_at IS NULL
                WHERE m.deleted_at IS NULL GROUP BY m.id ORDER BY m.name"""
                )
            )
            for row in rows:
                row["projects"] = self._rows(
                    conn.execute(
                        """SELECT p.id,p.name,p.status,pm.joined_at,pm.left_at FROM project_memberships pm
                    JOIN projects p ON p.id=pm.project_id WHERE pm.member_id=? AND p.deleted_at IS NULL
                    ORDER BY pm.left_at IS NULL DESC,p.updated_at DESC""",
                        (row["id"],),
                    )
                )
            return rows

    def save_member(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = required_text(payload, "name", 80)
        role = optional_text(payload, "role", 80)
        notes = optional_text(payload, "notes", 1000)
        member_id = payload.get("id")
        now = _now()
        with self._connection() as conn:
            if member_id:
                member_id = parse_id(member_id)
                cursor = conn.execute(
                    "UPDATE members SET name=?,role=?,notes=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                    (name, role, notes, now, member_id),
                )
                if not cursor.rowcount:
                    raise NotFoundError("成员不存在")
            else:
                member_id = conn.execute(
                    "INSERT INTO members(name,role,notes,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (name, role, notes, now, now),
                ).lastrowid
            return dict(
                conn.execute(
                    "SELECT * FROM members WHERE id=?", (member_id,)
                ).fetchone()
            )

    def delete_member(self, member_id: Any) -> None:
        member_id = parse_id(member_id)
        now = _now()
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE members SET deleted_at=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                (now, now, member_id),
            )
            if not cursor.rowcount:
                raise NotFoundError("成员不存在")
            conn.execute(
                "UPDATE project_memberships SET left_at=COALESCE(left_at,?),leave_reason='成员已删除' WHERE member_id=?",
                (_today().isoformat(), member_id),
            )

    def list_projects(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = self._rows(
                conn.execute(
                    """SELECT p.*,
                (SELECT work_date FROM work_logs w WHERE w.project_id=p.id
                  ORDER BY work_date DESC,id DESC LIMIT 1) latest_record_date
                FROM projects p WHERE deleted_at IS NULL ORDER BY updated_at DESC"""
                )
            )
            for row in rows:
                row["members"] = self._rows(
                    conn.execute(
                        """SELECT m.id,m.name,m.role FROM project_memberships pm JOIN members m ON m.id=pm.member_id
                    WHERE pm.project_id=? AND pm.left_at IS NULL AND m.deleted_at IS NULL ORDER BY m.name""",
                        (row["id"],),
                    )
                )
            return rows

    def list_project_overview(self, statuses: Any = None) -> list[dict[str, Any]]:
        """Return project IDs, names and statuses for the message overview command."""
        if isinstance(statuses, str):
            statuses = [statuses]
        requested = [str(status).strip() for status in (statuses or [])]
        requested = list(dict.fromkeys(status for status in requested if status))
        invalid = [status for status in requested if status not in PROJECT_STATUSES]
        if invalid:
            invalid_text = "、".join(dict.fromkeys(invalid))
            available = "、".join(PROJECT_STATUS_ORDER)
            raise ValidationError(
                f"项目状态输入错误：{invalid_text}\n"
                f"正确写法：/项目总览 [状态] [状态] ...\n"
                f"可用状态：{available}\n"
                "不填写状态时默认查询除结束外的项目。"
            )
        if not requested:
            requested = list(DEFAULT_PROJECT_OVERVIEW_STATUSES)
        placeholders = ",".join("?" * len(requested))
        with self._connection() as conn:
            return self._rows(
                conn.execute(
                    f"""SELECT id,name,status FROM projects
                    WHERE deleted_at IS NULL AND status IN ({placeholders})
                    ORDER BY id""",
                    requested,
                )
            )

    def list_default_summary_project_ids(self) -> list[int]:
        """Return the projects selected by default in the summary UI."""
        return [
            project["id"]
            for project in self.list_project_overview(ADVANCING_PROJECT_STATUSES)
        ]

    def save_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = required_text(payload, "name", 120)
        description = optional_text(payload, "description", 3000)
        status = str(payload.get("status", "准备"))
        if status not in PROJECT_STATUSES:
            raise ValidationError("无效的项目状态")
        start_date = (
            parse_date(payload["start_date"], "start_date")
            if payload.get("start_date")
            else None
        )
        end_date = (
            parse_date(payload["end_date"], "end_date")
            if payload.get("end_date")
            else None
        )
        if start_date and end_date and end_date < start_date:
            raise ValidationError("计划结束日期不能早于开始日期")
        member_ids = {
            parse_id(value, "member_id") for value in payload.get("member_ids", [])
        }
        project_id = payload.get("id")
        now = _now()
        today = _today().isoformat()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if member_ids:
                count = conn.execute(
                    f"SELECT COUNT(*) FROM members WHERE deleted_at IS NULL AND id IN ({','.join('?' * len(member_ids))})",
                    tuple(member_ids),
                ).fetchone()[0]
                if count != len(member_ids):
                    raise ValidationError("包含不存在的成员")
            if project_id:
                project_id = parse_id(project_id)
                old = conn.execute(
                    "SELECT * FROM projects WHERE id=? AND deleted_at IS NULL",
                    (project_id,),
                ).fetchone()
                if not old:
                    raise NotFoundError("项目不存在")
                conn.execute(
                    "UPDATE projects SET name=?,description=?,status=?,start_date=?,end_date=?,updated_at=? WHERE id=?",
                    (name, description, status, start_date, end_date, now, project_id),
                )
                if old["status"] != status:
                    conn.execute(
                        "INSERT INTO project_status_history(project_id,from_status,to_status,changed_at,note) VALUES(?,?,?,?,?)",
                        (
                            project_id,
                            old["status"],
                            status,
                            now,
                            optional_text(payload, "status_note", 500),
                        ),
                    )
            else:
                project_id = conn.execute(
                    "INSERT INTO projects(name,description,status,start_date,end_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                    (name, description, status, start_date, end_date, now, now),
                ).lastrowid
                conn.execute(
                    "INSERT INTO project_status_history(project_id,from_status,to_status,changed_at,note) VALUES(?,?,?,?,?)",
                    (project_id, None, status, now, "项目创建"),
                )
            active = {
                r[0]
                for r in conn.execute(
                    "SELECT member_id FROM project_memberships WHERE project_id=? AND left_at IS NULL",
                    (project_id,),
                )
            }
            for member_id in member_ids - active:
                conn.execute(
                    "INSERT INTO project_memberships(project_id,member_id,joined_at,join_reason) VALUES(?,?,?,?)",
                    (project_id, member_id, today, "项目配置变更"),
                )
            for member_id in active - member_ids:
                conn.execute(
                    "UPDATE project_memberships SET left_at=?,leave_reason=? WHERE project_id=? AND member_id=? AND left_at IS NULL",
                    (today, "项目配置变更", project_id, member_id),
                )
            return dict(
                conn.execute(
                    "SELECT * FROM projects WHERE id=?", (project_id,)
                ).fetchone()
            )

    def delete_project(self, project_id: Any) -> None:
        project_id = parse_id(project_id)
        now = _now()
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE projects SET deleted_at=?,updated_at=? WHERE id=? AND deleted_at IS NULL",
                (now, now, project_id),
            )
            if not cursor.rowcount:
                raise NotFoundError("项目不存在")
            conn.execute(
                "UPDATE project_memberships SET left_at=COALESCE(left_at,?),leave_reason='项目已删除' WHERE project_id=?",
                (_today().isoformat(), project_id),
            )

    def project_detail(self, project_id: Any) -> dict[str, Any]:
        project_id = parse_id(project_id)
        with self._connection() as conn:
            project = conn.execute(
                "SELECT * FROM projects WHERE id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if not project:
                raise NotFoundError("项目不存在")
            result = dict(project)
            result["status_history"] = self._rows(
                conn.execute(
                    "SELECT * FROM project_status_history WHERE project_id=? ORDER BY changed_at DESC,id DESC",
                    (project_id,),
                )
            )
            result["membership_history"] = self._rows(
                conn.execute(
                    """SELECT pm.*,m.name FROM project_memberships pm JOIN members m ON m.id=pm.member_id
                WHERE pm.project_id=? ORDER BY joined_at DESC,pm.id DESC""",
                    (project_id,),
                )
            )
            result["work_logs"] = self._rows(
                conn.execute(
                    """SELECT w.* FROM work_logs w
                WHERE w.project_id=? ORDER BY work_date DESC,w.id DESC LIMIT 60""",
                    (project_id,),
                )
            )
            return result

    def save_work_log(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = parse_id(payload.get("project_id"), "project_id")
        log_id = parse_id(payload.get("id"), "id") if payload.get("id") else None
        work_date = parse_date(payload.get("work_date"), "work_date")
        content = required_text(payload, "content", 4000)
        now = _now()
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM projects WHERE id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone():
                raise NotFoundError("项目不存在")
            if log_id:
                cursor = conn.execute(
                    """UPDATE work_logs SET work_date=?,content=?,updated_at=?
                    WHERE id=? AND project_id=?""",
                    (work_date, content, now, log_id, project_id),
                )
                if not cursor.rowcount:
                    raise NotFoundError("工作记录不存在")
            else:
                log_id = conn.execute(
                    "INSERT INTO work_logs(project_id,work_date,content,created_at,updated_at) VALUES(?,?,?,?,?)",
                    (project_id, work_date, content, now, now),
                ).lastrowid
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE id=?", (now, project_id)
            )
            return dict(
                conn.execute("SELECT * FROM work_logs WHERE id=?", (log_id,)).fetchone()
            )

    def list_work_logs(self, project_id: Any, work_date: Any) -> dict[str, Any]:
        project_id = parse_id(project_id, "project_id")
        work_date = parse_date(work_date, "date")
        with self._connection() as conn:
            project = conn.execute(
                "SELECT id,name FROM projects WHERE id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone()
            if not project:
                raise NotFoundError("项目不存在")
            logs = self._rows(
                conn.execute(
                    """SELECT id,project_id,work_date,content,created_at,updated_at
                    FROM work_logs WHERE project_id=? AND work_date=? ORDER BY id""",
                    (project_id, work_date),
                )
            )
        return {"project": dict(project), "date": work_date, "logs": logs}

    def delete_work_log(self, log_id: Any) -> None:
        log_id = parse_id(log_id, "id")
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM work_logs WHERE id=?", (log_id,))
            if not cursor.rowcount:
                raise NotFoundError("工作记录不存在")

    def timeline(self, start: str, end: str) -> dict[str, Any]:
        start_date = date.fromisoformat(parse_date(start, "start"))
        end_date = date.fromisoformat(parse_date(end, "end"))
        if (end_date - start_date).days > 62:
            raise ValidationError("进程表最多查询 63 天")
        dates = [d.isoformat() for d in date_range(start_date, end_date)]
        projects = self.list_projects()
        with self._connection() as conn:
            logs = self._rows(
                conn.execute(
                    """SELECT w.id,w.project_id,w.work_date,w.content FROM work_logs w
                WHERE w.work_date BETWEEN ? AND ? ORDER BY w.id""",
                    (start, end),
                )
            )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for log in logs:
            grouped.setdefault(f"{log['project_id']}:{log['work_date']}", []).append(
                log
            )
        return {"dates": dates, "projects": projects, "logs": grouped}

    def get_calendar(self, month: str) -> dict[str, Any]:
        try:
            start, end = month_bounds(month)
        except ValueError as exc:
            raise ValidationError("month 必须是 YYYY-MM 格式") from exc
        with self._connection() as conn:
            overrides = {
                r["work_date"]: dict(r)
                for r in conn.execute(
                    "SELECT * FROM calendar_days WHERE work_date BETWEEN ? AND ?",
                    (start.isoformat(), end.isoformat()),
                )
            }
        days = []
        for day in date_range(start, end):
            value, source = default_is_workday(day)
            if day.isoformat() in overrides:
                item = overrides[day.isoformat()]
                value, source = bool(item["is_workday"]), item["source"]
            days.append(
                {
                    "date": day.isoformat(),
                    "is_workday": value,
                    "source": source,
                    "weekday": day.weekday(),
                }
            )
        return {
            "month": month,
            "workdays": sum(d["is_workday"] for d in days),
            "days": days,
        }

    def save_calendar_day(self, payload: dict[str, Any]) -> None:
        work_date = parse_date(payload.get("date"))
        if not isinstance(payload.get("is_workday"), bool):
            raise ValidationError("is_workday 必须是布尔值")
        note = optional_text(payload, "note", 300)
        with self._connection() as conn:
            conn.execute(
                """INSERT INTO calendar_days(work_date,is_workday,source,note,updated_at) VALUES(?,?,?,?,?)
                ON CONFLICT(work_date) DO UPDATE SET is_workday=excluded.is_workday,source='人工调整',note=excluded.note,updated_at=excluded.updated_at""",
                (work_date, int(payload["is_workday"]), "人工调整", note, _now()),
            )

    def attendance_matrix(self, month: str) -> dict[str, Any]:
        calendar = self.get_calendar(month)
        members = self.list_members()
        start, end = month_bounds(month)
        with self._connection() as conn:
            records = self._rows(
                conn.execute(
                    "SELECT * FROM attendance_records WHERE work_date BETWEEN ? AND ?",
                    (start.isoformat(), end.isoformat()),
                )
            )
            memberships = self._rows(
                conn.execute(
                    """SELECT pm.member_id,pm.project_id,p.name,pm.joined_at,pm.left_at
                    FROM project_memberships pm JOIN projects p ON p.id=pm.project_id
                    WHERE p.deleted_at IS NULL AND pm.joined_at<=?
                    AND (pm.left_at IS NULL OR pm.left_at>=?)""",
                    (end.isoformat(), start.isoformat()),
                )
            )
            histories = self._rows(
                conn.execute(
                    """SELECT project_id,to_status,changed_at FROM project_status_history
                    ORDER BY changed_at ASC,id ASC"""
                )
            )
        histories_by_project: dict[int, list[dict[str, Any]]] = {}
        for history in histories:
            histories_by_project.setdefault(history["project_id"], []).append(history)
        explicit = {f"{r['member_id']}:{r['work_date']}": r for r in records}
        assignments: dict[str, list[str]] = {}
        for item in memberships:
            for day in calendar["days"]:
                day_value = day["date"]
                if item["joined_at"] <= day_value and (
                    item["left_at"] is None or item["left_at"] >= day_value
                ) and self._status_on_date(
                    histories_by_project.get(item["project_id"], []), day_value
                ) in ADVANCING_PROJECT_STATUSES:
                    assignments.setdefault(
                        f"{item['member_id']}:{day_value}", []
                    ).append(item["name"])
        return {
            "calendar": calendar,
            "members": members,
            "records": explicit,
            "assignments": assignments,
            "today": _today().isoformat(),
        }

    @staticmethod
    def _status_on_date(history: list[dict[str, Any]], work_date: str) -> str | None:
        status = None
        for item in history:
            if item["changed_at"][:10] > work_date:
                break
            status = item["to_status"]
        return status

    def save_attendance(self, payload: dict[str, Any]) -> None:
        member_id = parse_id(payload.get("member_id"), "member_id")
        work_date = parse_date(payload.get("date"))
        status = str(payload.get("status", "正常"))
        if status not in ATTENDANCE_STATUSES:
            raise ValidationError("无效的考勤状态")
        raw_hours = payload.get("hours")
        hours = 8.0 if raw_hours in (None, "") else float(raw_hours)
        if hours < 0 or hours > 24:
            raise ValidationError("时长必须在 0 到 24 之间")
        note = optional_text(payload, "note", 500)
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM members WHERE id=? AND deleted_at IS NULL", (member_id,)
            ).fetchone():
                raise NotFoundError("成员不存在")
            conn.execute(
                """INSERT INTO attendance_records(member_id,work_date,status,hours,note,updated_at) VALUES(?,?,?,?,?,?)
                ON CONFLICT(member_id,work_date) DO UPDATE SET status=excluded.status,hours=excluded.hours,note=excluded.note,updated_at=excluded.updated_at""",
                (member_id, work_date, status, hours, note, _now()),
            )

    def attendance_summary(self, start: str, end: str) -> dict[str, Any]:
        start = parse_date(start, "start")
        end = parse_date(end, "end")
        if end < start:
            raise ValidationError("end 不能早于 start")
        end = min(end, _today().isoformat())
        with self._connection() as conn:
            members = self._rows(
                conn.execute(
                    "SELECT id,name FROM members WHERE deleted_at IS NULL ORDER BY name"
                )
            )
            records = self._rows(
                conn.execute(
                    "SELECT * FROM attendance_records WHERE work_date BETWEEN ? AND ?",
                    (start, end),
                )
            )
            overrides = {
                r["work_date"]: bool(r["is_workday"])
                for r in conn.execute(
                    "SELECT work_date,is_workday FROM calendar_days WHERE work_date BETWEEN ? AND ?",
                    (start, end),
                )
            }
            details = self._rows(
                conn.execute(
                    """SELECT a.work_date,a.status,a.hours,a.note,m.name FROM attendance_records a
                JOIN members m ON m.id=a.member_id WHERE a.work_date BETWEEN ? AND ? AND a.status IN ('加班','调休')
                ORDER BY a.work_date DESC,m.name""",
                    (start, end),
                )
            )
        workdays = set()
        for day in date_range(date.fromisoformat(start), date.fromisoformat(end)):
            default, _ = default_is_workday(day)
            if overrides.get(day.isoformat(), default):
                workdays.add(day.isoformat())
        by_member: dict[int, list[dict[str, Any]]] = {}
        for record in records:
            by_member.setdefault(record["member_id"], []).append(record)
        summary = []
        for member in members:
            own = by_member.get(member["id"], [])
            non_normal_workdays = {
                r["work_date"]
                for r in own
                if r["status"] != "正常" and r["work_date"] in workdays
            }
            explicit_normal_rest = {
                r["work_date"]
                for r in own
                if r["status"] == "正常" and r["work_date"] not in workdays
            }
            summary.append(
                {
                    **member,
                    "normal_days": len(workdays - non_normal_workdays)
                    + len(explicit_normal_rest),
                    "overtime_days": len(
                        {r["work_date"] for r in own if r["status"] == "加班"}
                    ),
                    "time_off_days": len(
                        {r["work_date"] for r in own if r["status"] == "调休"}
                    ),
                    "overtime_hours": sum(
                        r["hours"] for r in own if r["status"] == "加班"
                    ),
                }
            )
        return {"summary": summary, "details": details, "start": start, "end": end}

    def team_summary_material(
        self, project_ids: Any, summary_date: Any, history_days: Any = 3
    ) -> tuple[list[int], str]:
        summary_date = parse_date(summary_date, "summary_date")
        try:
            history_days = int(history_days)
        except (TypeError, ValueError) as exc:
            raise ValidationError("history_days 必须是整数") from exc
        if history_days < 0 or history_days > 30:
            raise ValidationError("history_days 必须在 0 到 30 之间")
        requested = (
            []
            if not project_ids
            else [parse_id(value, "project_id") for value in project_ids]
        )
        with self._connection() as conn:
            if requested:
                placeholders = ",".join("?" * len(requested))
                projects = self._rows(
                    conn.execute(
                        f"SELECT id,name,status FROM projects WHERE deleted_at IS NULL AND id IN ({placeholders}) ORDER BY name",
                        requested,
                    )
                )
                if len(projects) != len(set(requested)):
                    raise ValidationError("包含不存在的项目")
            else:
                projects = self._rows(
                    conn.execute(
                        "SELECT id,name,status FROM projects WHERE deleted_at IS NULL ORDER BY name"
                    )
                )
            sections = []
            for project in projects:
                logs = self._rows(
                    conn.execute(
                        "SELECT content FROM work_logs WHERE project_id=? AND work_date=? ORDER BY id",
                        (project["id"], summary_date),
                    )
                )
                sections.append(
                    f"项目：{project['name']}\n状态：{project['status']}\n今日记录：\n"
                    + ("\n".join(f"- {log['content']}" for log in logs) or "- 无记录")
                )
            history = []
            if history_days:
                history_start = (
                    date.fromisoformat(summary_date) - timedelta(days=history_days)
                ).isoformat()
                history = self._rows(
                    conn.execute(
                        """SELECT summary_date,content FROM team_daily_summaries
                        WHERE summary_date BETWEEN ? AND ? ORDER BY summary_date""",
                        (
                            history_start,
                            (date.fromisoformat(summary_date) - timedelta(days=1)).isoformat(),
                        ),
                    )
                )
        if not projects:
            raise ValidationError("没有可生成日报的项目")
        material = f"日期：{summary_date}\n\n" + "\n\n".join(sections)
        if history:
            material += "\n\n前几天已生成的团队总结：\n" + "\n\n".join(
                f"[{item['summary_date']}]\n{item['content']}" for item in history
            )
        return [project["id"] for project in projects], material

    def save_team_summary(
        self,
        project_ids: list[int],
        summary_date: str,
        provider_id: str,
        content: str,
        overwrite: bool = False,
    ) -> bool:
        summary_date = parse_date(summary_date, "summary_date")
        content = str(content).strip()
        if not content:
            raise ValidationError("日报内容不能为空")
        with self._connection() as conn:
            if overwrite:
                conn.execute(
                    """INSERT INTO team_daily_summaries(summary_date,project_ids,provider_id,content,created_at) VALUES(?,?,?,?,?)
                    ON CONFLICT(summary_date) DO UPDATE SET project_ids=excluded.project_ids,provider_id=excluded.provider_id,content=excluded.content,created_at=excluded.created_at""",
                    (summary_date, json.dumps(project_ids), provider_id, content, _now()),
                )
                return True
            cursor = conn.execute(
                """INSERT OR IGNORE INTO team_daily_summaries
                (summary_date,project_ids,provider_id,content,created_at) VALUES(?,?,?,?,?)""",
                (summary_date, json.dumps(project_ids), provider_id, content, _now()),
            )
            return bool(cursor.rowcount)

    def get_team_summary(self, summary_date: Any) -> dict[str, Any] | None:
        summary_date = parse_date(summary_date, "summary_date")
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM team_daily_summaries WHERE summary_date=?",
                (summary_date,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["project_ids"] = json.loads(result["project_ids"])
            if result["project_ids"]:
                placeholders = ",".join("?" * len(result["project_ids"]))
                names = {
                    item["id"]: item["name"]
                    for item in conn.execute(
                        f"SELECT id,name FROM projects WHERE id IN ({placeholders})",
                        result["project_ids"],
                    )
                }
                result["project_names"] = [
                    names[item] for item in result["project_ids"] if item in names
                ]
            else:
                result["project_names"] = []
            return result

    def list_team_summaries(self, start: Any, end: Any) -> dict[str, Any]:
        start_date = date.fromisoformat(parse_date(start, "start"))
        end_date = date.fromisoformat(parse_date(end, "end"))
        if end_date < start_date:
            raise ValidationError("end 不能早于 start")
        if (end_date - start_date).days > 62:
            raise ValidationError("项目总结最多查询 63 天")
        dates = [day.isoformat() for day in date_range(start_date, end_date)]
        with self._connection() as conn:
            summaries = self._rows(
                conn.execute(
                    """SELECT * FROM team_daily_summaries
                    WHERE summary_date BETWEEN ? AND ? ORDER BY summary_date""",
                    (start_date.isoformat(), end_date.isoformat()),
                )
            )
            project_ids = {
                project_id
                for summary in summaries
                for project_id in json.loads(summary["project_ids"])
            }
            names = {}
            if project_ids:
                placeholders = ",".join("?" * len(project_ids))
                names = {
                    row["id"]: row["name"]
                    for row in conn.execute(
                        f"SELECT id,name FROM projects WHERE id IN ({placeholders})",
                        sorted(project_ids),
                    )
                }
        for summary in summaries:
            summary["project_ids"] = json.loads(summary["project_ids"])
            summary["project_names"] = [
                names[project_id]
                for project_id in summary["project_ids"]
                if project_id in names
            ]
        return {"dates": dates, "summaries": summaries}

    def update_team_summary_content(self, summary_date: Any, content: Any) -> dict[str, Any]:
        summary_date = parse_date(summary_date, "summary_date")
        content = str(content).strip()
        if not content:
            raise ValidationError("日报内容不能为空")
        if len(content) > 20000:
            raise ValidationError("日报内容最多 20000 个字符")
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE team_daily_summaries SET content=?,created_at=? WHERE summary_date=?",
                (content, _now(), summary_date),
            )
            if not cursor.rowcount:
                raise NotFoundError("当天尚未生成日报")
        return self.get_team_summary(summary_date)

    def delete_team_summary(self, summary_date: Any) -> None:
        summary_date = parse_date(summary_date, "summary_date")
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM team_daily_summaries WHERE summary_date=?",
                (summary_date,),
            )
            if not cursor.rowcount:
                raise NotFoundError("该日尚未生成项目总结")

    def update_status_history(self, payload: dict[str, Any]) -> None:
        history_id = parse_id(payload.get("id"), "id")
        project_id = parse_id(payload.get("project_id"), "project_id")
        from_status = str(payload.get("from_status") or "").strip() or None
        to_status = str(payload.get("to_status") or "").strip()
        if from_status is not None and from_status not in PROJECT_STATUSES:
            raise ValidationError("无效的原状态")
        if to_status not in PROJECT_STATUSES:
            raise ValidationError("无效的目标状态")
        changed_at = parse_datetime(payload.get("changed_at"), "changed_at")
        note = optional_text(payload, "note", 500)
        with self._connection() as conn:
            cursor = conn.execute(
                """UPDATE project_status_history SET from_status=?,to_status=?,changed_at=?,note=?
                WHERE id=? AND project_id=?""",
                (from_status, to_status, changed_at, note, history_id, project_id),
            )
            if not cursor.rowcount:
                raise NotFoundError("状态历史不存在")
            self._sync_project_status(conn, project_id)

    def insert_status_history(self, payload: dict[str, Any]) -> None:
        project_id = parse_id(payload.get("project_id"), "project_id")
        from_status = str(payload.get("from_status") or "").strip() or None
        to_status = str(payload.get("to_status") or "").strip()
        if from_status is not None and from_status not in PROJECT_STATUSES:
            raise ValidationError("无效的原状态")
        if to_status not in PROJECT_STATUSES:
            raise ValidationError("无效的目标状态")
        changed_at = parse_datetime(payload.get("changed_at"), "changed_at")
        note = optional_text(payload, "note", 500)
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM projects WHERE id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone():
                raise NotFoundError("项目不存在")
            conn.execute(
                """INSERT INTO project_status_history
                (project_id,from_status,to_status,changed_at,note) VALUES(?,?,?,?,?)""",
                (project_id, from_status, to_status, changed_at, note),
            )
            self._sync_project_status(conn, project_id)

    def delete_status_history(self, history_id: Any, project_id: Any) -> None:
        history_id = parse_id(history_id, "id")
        project_id = parse_id(project_id, "project_id")
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM project_status_history WHERE id=? AND project_id=?",
                (history_id, project_id),
            )
            if not cursor.rowcount:
                raise NotFoundError("状态历史不存在")
            self._sync_project_status(conn, project_id)

    @staticmethod
    def _sync_project_status(conn: sqlite3.Connection, project_id: int) -> None:
        latest = conn.execute(
            """SELECT to_status FROM project_status_history WHERE project_id=?
            ORDER BY changed_at DESC,id DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if latest:
            conn.execute(
                "UPDATE projects SET status=?,updated_at=? WHERE id=?",
                (latest["to_status"], _now(), project_id),
            )

    def update_membership_history(self, payload: dict[str, Any]) -> None:
        membership_id = parse_id(payload.get("id"), "id")
        project_id = parse_id(payload.get("project_id"), "project_id")
        joined_at, left_at, join_reason, leave_reason = self._membership_values(payload)
        with self._connection() as conn:
            item = conn.execute(
                "SELECT member_id FROM project_memberships WHERE id=? AND project_id=?",
                (membership_id, project_id),
            ).fetchone()
            if not item:
                raise NotFoundError("成员变动历史不存在")
            if (
                left_at is None
                and conn.execute(
                    """SELECT 1 FROM project_memberships WHERE project_id=? AND member_id=?
                AND left_at IS NULL AND id<>?""",
                    (project_id, item["member_id"], membership_id),
                ).fetchone()
            ):
                raise ValidationError("该成员已经存在另一条进行中的参与记录")
            conn.execute(
                """UPDATE project_memberships SET joined_at=?,left_at=?,join_reason=?,leave_reason=?
                WHERE id=?""",
                (joined_at, left_at, join_reason, leave_reason, membership_id),
            )

    def insert_membership_history(self, payload: dict[str, Any]) -> None:
        project_id = parse_id(payload.get("project_id"), "project_id")
        member_id = parse_id(payload.get("member_id"), "member_id")
        joined_at, left_at, join_reason, leave_reason = self._membership_values(payload)
        with self._connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM projects WHERE id=? AND deleted_at IS NULL",
                (project_id,),
            ).fetchone():
                raise NotFoundError("项目不存在")
            if not conn.execute(
                "SELECT 1 FROM members WHERE id=? AND deleted_at IS NULL", (member_id,)
            ).fetchone():
                raise NotFoundError("成员不存在")
            if left_at is None and conn.execute(
                """SELECT 1 FROM project_memberships WHERE project_id=? AND member_id=?
                AND left_at IS NULL""",
                (project_id, member_id),
            ).fetchone():
                raise ValidationError("该成员已经参与此项目")
            conn.execute(
                """INSERT INTO project_memberships
                (project_id,member_id,joined_at,left_at,join_reason,leave_reason)
                VALUES(?,?,?,?,?,?)""",
                (project_id, member_id, joined_at, left_at, join_reason, leave_reason),
            )

    def delete_membership_history(self, membership_id: Any, project_id: Any) -> None:
        membership_id = parse_id(membership_id, "id")
        project_id = parse_id(project_id, "project_id")
        with self._connection() as conn:
            cursor = conn.execute(
                "DELETE FROM project_memberships WHERE id=? AND project_id=?",
                (membership_id, project_id),
            )
            if not cursor.rowcount:
                raise NotFoundError("成员变动历史不存在")

    @staticmethod
    def _membership_values(payload: dict[str, Any]) -> tuple[str, str | None, str, str]:
        joined_at = parse_date(payload.get("joined_at"), "joined_at")
        left_at = (
            parse_date(payload.get("left_at"), "left_at")
            if payload.get("left_at")
            else None
        )
        if left_at and left_at < joined_at:
            raise ValidationError("退出日期不能早于加入日期")
        return (
            joined_at,
            left_at,
            optional_text(payload, "join_reason", 500),
            optional_text(payload, "leave_reason", 500),
        )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _today() -> date:
    return datetime.now().astimezone().date()


SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_members_active_name ON members(name) WHERE deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS projects (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('准备','进行','维护','暂停','结束')),
 start_date TEXT, end_date TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status, deleted_at);
CREATE TABLE IF NOT EXISTS project_status_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
 from_status TEXT, to_status TEXT NOT NULL, changed_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_memberships (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
 member_id INTEGER NOT NULL REFERENCES members(id), joined_at TEXT NOT NULL, left_at TEXT,
 join_reason TEXT NOT NULL DEFAULT '', leave_reason TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_membership_active ON project_memberships(project_id,member_id) WHERE left_at IS NULL;
CREATE TABLE IF NOT EXISTS work_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL REFERENCES projects(id),
 work_date TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_logs_project_date ON work_logs(project_id,work_date);
CREATE TABLE IF NOT EXISTS calendar_days (
 work_date TEXT PRIMARY KEY, is_workday INTEGER NOT NULL CHECK(is_workday IN (0,1)),
 source TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attendance_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT, member_id INTEGER NOT NULL REFERENCES members(id),
 work_date TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('正常','加班','调休')),
 hours REAL NOT NULL DEFAULT 0 CHECK(hours>=0 AND hours<=24), note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
 UNIQUE(member_id,work_date)
);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance_records(work_date,status);
CREATE TABLE IF NOT EXISTS team_daily_summaries (
 id INTEGER PRIMARY KEY AUTOINCREMENT, summary_date TEXT NOT NULL UNIQUE,
 project_ids TEXT NOT NULL, provider_id TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
);
"""
