import tempfile
import unittest
from pathlib import Path
from ppm.database import Database
from ppm.storage import prepare_data
from ppm.errors import ValidationError, NotFoundError

class TodoTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/"ppm.sqlite3"
        self.db=Database(self.path);self.db.initialize()
        self.project=self.db.save_project({"name":"关联项目"})["id"]
    def tearDown(self): self.temp.cleanup()
    def test_deleted_project_and_partial_update(self):
        todo=self.db.save_todo({"project_id":self.project,"content":"记录"})
        self.db.delete_project(self.project)
        changed=self.db.save_todo({"id":todo["id"],"status":"已完成"})
        self.assertEqual(changed["project_id"],self.project)
        self.assertEqual(changed["content"],"记录")
        self.assertTrue(self.db.list_todos()[0]["project_deleted_at"])
        with self.assertRaises(ValidationError): self.db.save_todo({"project_id":self.project,"content":"新记录"})
        self.db.save_todo({"id":todo["id"],"project_id":None})
    def test_validation_and_completion_time(self):
        for payload in ({"content":""},{"content":"x","project_id":999},{"content":"x","status":"取消"}):
            with self.assertRaises(ValidationError): self.db.save_todo(payload)
        todo=self.db.save_todo({"content":"不关联","status":"已完成"})
        changed=self.db.save_todo({"id":todo["id"],"content":"编辑完成事项"})
        self.assertEqual(changed["completed_at"],todo["completed_at"])
        self.db.save_todo({"id":todo["id"],"status":"未完成"})
        self.assertIsNone(self.db.list_todos()[0]["completed_at"])
    def test_archive_restored_once_without_reminders(self):
        with self.db._connection() as conn:
            conn.execute("CREATE TABLE legacy_todos(id INTEGER PRIMARY KEY,project_id INTEGER,content TEXT,status TEXT,created_at TEXT,updated_at TEXT,deleted_at TEXT,push_enabled INTEGER)")
            conn.execute("INSERT INTO legacy_todos VALUES(7,?,'旧待办','已完成','2026-01-01','2026-01-01',NULL,1)",(self.project,))
            conn.execute("PRAGMA user_version=4")
        migrated=prepare_data(self.path.parent)
        self.assertEqual(migrated.list_todos()[0]["id"],7)
        self.assertEqual(migrated.list_todos()[0]["status"],"已完成")
        self.assertNotIn("push_enabled",migrated.list_todos()[0])
        migrated.delete_todo(7)
        self.assertEqual(prepare_data(self.path.parent).list_todos(),[])
        self.assertEqual(len(list(self.path.parent.glob("backups/*.sqlite3"))),1)
