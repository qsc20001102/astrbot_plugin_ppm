"""Project tasks are separate from scheduled reminder todos.

All task mutations share the caller's SQLite transaction with logs and audit
snapshots. Deleted tasks remain available for historical traceability.
"""
from datetime import date, datetime
import json

from .errors import NotFoundError, ValidationError
from .validation import optional_text, parse_date, parse_id, required_text


TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project_id INTEGER NOT NULL REFERENCES projects(id),
 name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('未完成','完成')),
 start_date TEXT NOT NULL, completed_date TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON project_tasks(project_id,deleted_at);
CREATE TABLE IF NOT EXISTS task_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id INTEGER NOT NULL REFERENCES project_tasks(id),
 event TEXT NOT NULL, changed_at TEXT NOT NULL, snapshot TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_history ON task_history(task_id,id);
"""


class TaskRepository:
    @staticmethod
    def _require_project(conn, project_id):
        if not conn.execute("SELECT 1 FROM projects WHERE id=? AND deleted_at IS NULL", (project_id,)).fetchone():
            raise NotFoundError("项目不存在")

    @staticmethod
    def _progress(tasks):
        active = [task for task in tasks if not task.get("deleted_at")]
        done = sum(task["status"] == "完成" for task in active)
        return {"task_count": len(active), "completed_task_count": done,
                "progress": int(done * 100 / len(active) + .5) if active else 0}

    @staticmethod
    def _audit_task(conn, task_id, event, now):
        task = dict(conn.execute("SELECT * FROM project_tasks WHERE id=?", (task_id,)).fetchone())
        conn.execute("INSERT INTO task_history(task_id,event,changed_at,snapshot) VALUES(?,?,?,?)",
                     (task_id, event, now, json.dumps(task, ensure_ascii=False)))

    def _project_tasks(self, conn, project_id):
        tasks = self._rows(conn.execute("SELECT * FROM project_tasks WHERE project_id=? ORDER BY start_date,id", (project_id,)))
        histories = {}
        for row in conn.execute("SELECT h.* FROM task_history h JOIN project_tasks t ON t.id=h.task_id WHERE t.project_id=? ORDER BY h.id", (project_id,)):
            event = dict(row)
            event["snapshot"] = json.loads(event["snapshot"])
            histories.setdefault(event["task_id"], []).append(event)
        today = datetime.now().astimezone().date()
        for task in tasks:
            end = task["completed_date"] or (task["deleted_at"] or today.isoformat())[:10]
            task["duration_days"] = max(0, (date.fromisoformat(end) - date.fromisoformat(task["start_date"])).days + 1)
            task["history"] = histories.get(task["id"], [])
        return tasks

    def list_tasks(self, project_id):
        project_id = parse_id(project_id, "project_id")
        with self._connection() as conn:
            self._require_project(conn, project_id)
            return self._project_tasks(conn, project_id)

    def _task_summary_material(self, conn, project_id, summary_date, logs):
        """Use audit snapshots to keep later changes out of historical reports.

        Work logs are editable, so the content reflects the latest correction to
        that day's records; task state is the last snapshot known on that day.
        """
        grouped = {}
        for log in logs:
            grouped.setdefault(log["task_id"], []).append({"id": log["id"], "content": log["content"]})
        snapshots, tasks = [], []
        for task in self._project_tasks(conn, project_id):
            history = [h for h in task["history"] if h["changed_at"][:10] <= summary_date]
            history.sort(key=lambda h: (h["changed_at"], h["id"]))
            snapshot = history[-1]["snapshot"] if history else None
            records = grouped.get(task["id"], [])
            if snapshot:
                snapshots.append(snapshot)
            if not snapshot and not records:
                continue  # A task created later is not historical context.
            events = [{"event": h["event"], "changed_at": h["changed_at"],
                       "status": h["snapshot"]["status"],
                       "start_date": h["snapshot"]["start_date"],
                       "completed_date": h["snapshot"]["completed_date"]}
                      for h in history if h["changed_at"][:10] == summary_date]
            tasks.append({
                "task_id": task["id"],
                "name": snapshot["name"] if snapshot else f"任务 #{task['id']}（缺少当日快照）",
                "status_as_of_date": snapshot["status"] if snapshot else "未知",
                "start_date": snapshot["start_date"] if snapshot else None,
                "completed_date": snapshot["completed_date"] if snapshot else None,
                "deleted_as_of_date": bool(snapshot and snapshot["deleted_at"]),
                "changes_on_date": events,
                "records_on_date": records,
            })
        return {"task_progress_as_of_date": self._progress(snapshots),
                "tasks": tasks, "project_records_on_date": grouped.get(None, []),
                "unknown_task_count": sum(t["status_as_of_date"] == "未知" for t in tasks)}

    def save_task(self, payload):
        project_id = parse_id(payload.get("project_id"), "project_id")
        task_id = parse_id(payload["id"]) if payload.get("id") is not None else None
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_project(conn, project_id)
            old = None
            if task_id is not None:
                old = conn.execute("SELECT * FROM project_tasks WHERE id=? AND project_id=? AND deleted_at IS NULL", (task_id, project_id)).fetchone()
                if not old:
                    raise NotFoundError("任务不存在或不属于当前项目")
            values = {**(dict(old) if old else {}), **payload}
            name = required_text(values, "name", 120)
            description = optional_text(values, "description", 3000)
            status = values.get("status", "未完成")
            if status not in ("完成", "未完成"):
                raise ValidationError("任务状态必须是完成或未完成")
            start = parse_date(values.get("start_date", now[:10]), "start_date")
            completed = None
            if status == "完成":
                completed = parse_date(values.get("completed_date") or now[:10], "completed_date")
                if completed < start or completed > now[:10]:
                    raise ValidationError("完成日期不能早于开始日期或晚于今天")
            elif payload.get("completed_date"):
                raise ValidationError("未完成任务不能设置完成日期")
            if old:
                if all(old[key] == value for key, value in zip(
                    ("name", "description", "status", "start_date", "completed_date"),
                    (name, description, status, start, completed))):
                    return dict(old)
                conn.execute("UPDATE project_tasks SET name=?,description=?,status=?,start_date=?,completed_date=?,updated_at=? WHERE id=?",
                             (name, description, status, start, completed, now, task_id))
                event = ("完成" if status == "完成" else "重新打开") if status != old["status"] else "修改"
            else:
                task_id = conn.execute("INSERT INTO project_tasks(project_id,name,description,status,start_date,completed_date,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                                       (project_id, name, description, status, start, completed, now, now)).lastrowid
                event = "创建"
            self._audit_task(conn, task_id, event, now)
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
            return dict(conn.execute("SELECT * FROM project_tasks WHERE id=?", (task_id,)).fetchone())

    def delete_task(self, task_id, project_id):
        task_id, project_id = parse_id(task_id), parse_id(project_id, "project_id")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._require_project(conn, project_id)
            cursor = conn.execute("UPDATE project_tasks SET deleted_at=?,updated_at=? WHERE id=? AND project_id=? AND deleted_at IS NULL", (now, now, task_id, project_id))
            if not cursor.rowcount:
                raise NotFoundError("任务不存在或不属于当前项目")
            self._audit_task(conn, task_id, "删除", now)
            conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))

    def _log_task(self, conn, payload, old, project_id, work_date, now):
        task_id = payload.get("task_id", old["task_id"] if old else None)
        action = payload.get("task_action", "keep")
        if action not in ("keep", "complete", "reopen"):
            raise ValidationError("无效的任务完成操作")
        if task_id is None or task_id == "":
            if action != "keep":
                raise ValidationError("请先选择关联任务")
            return None
        task_id = parse_id(task_id, "task_id")
        task = conn.execute("SELECT * FROM project_tasks WHERE id=? AND project_id=?", (task_id, project_id)).fetchone()
        if not task or (task["deleted_at"] and (not old or old["task_id"] != task_id or action != "keep")):
            raise ValidationError("任务不存在、已删除或不属于当前项目")
        if action == "keep":
            return task_id
        status = "完成" if action == "complete" else "未完成"
        if status == task["status"]:
            return task_id
        completed = work_date if action == "complete" else None
        if completed and (completed < task["start_date"] or completed > now[:10]):
            raise ValidationError("完成日期不能早于开始日期或晚于今天")
        conn.execute("UPDATE project_tasks SET status=?,completed_date=?,updated_at=? WHERE id=?", (status, completed, now, task_id))
        self._audit_task(conn, task_id, "完成" if completed else "重新打开", now)
        return task_id
