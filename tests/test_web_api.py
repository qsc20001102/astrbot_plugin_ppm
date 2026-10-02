from datetime import date
TODAY = date.today().isoformat()
from contextlib import closing
import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from ppm.server import create_app
from ppm.settings import Settings
from ppm.llm import ModelClient
from ppm.errors import ValidationError
from ppm.storage import prepare_data, import_database
from ppm.database import Database

class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.client = TestClient(TestServer(create_app(self.root, password="")))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def post(self, endpoint, data):
        response = await self.client.post("/api/"+endpoint, json=data)
        self.assertEqual(response.status, 200, await response.text())
        return (await response.json())["data"]

    async def test_real_crud_and_summary(self):
        member = await self.post("members/save", {"name":"甲"})
        project = await self.post("projects/save", {"name":"独立项目", "member_ids":[member["id"]]})
        task = await self.post("tasks/save", {"project_id":project["id"],"name":"开发", "start_date":TODAY})
        await self.post("worklogs/save", {"project_id":project["id"],"task_id":task["id"],"work_date":TODAY,"content":"完成联调","task_action":"complete"})
        await self.post("settings/save", {"ai_model":"test-model", "ai_api_key":"secret"})
        with patch("ppm.llm.ModelClient.generate", AsyncMock(return_value="日报")) as model:
            result = await self.post("ai/summary", {"project_ids":[project["id"]],"date":TODAY,"overwrite":True})
            self.assertEqual(result["content"], "日报")
            self.assertIn("最后历史快照", model.call_args.kwargs["system_prompt"])
        response = await self.client.get(f"/api/summaries?start={TODAY}&end={TODAY}")
        self.assertEqual((await response.json())["data"]["summaries"][0]["content"], "日报")
        detail = await self.client.get(f"/api/projects/detail?id={project['id']}")
        self.assertEqual(detail.status, 200)
        for path in ("members", "projects", "dashboard", "health", "attendance", "attendance/summary", "calendar", "timeline"):
            self.assertEqual((await self.client.get("/api/"+path)).status, 200, path)
        for path in ("ai/project-summary",):
            self.assertEqual((await self.client.get("/api/"+path)).status, 404)

    async def test_saved_prompt_reaches_model_and_preview_preserves_saved_summary(self):
        received = []

        async def complete(request):
            received.append(await request.json())
            return web.json_response({"choices": [{"message": {"content": f"结果{len(received)}"}}]})

        app = web.Application()
        app.router.add_post("/v1/chat/completions", complete)
        server = TestServer(app)
        await server.start_server()
        try:
            project = await self.post("projects/save", {"name": "提示词验证"})
            await self.post("settings/save", {
                "ai_base_url": str(server.make_url("/v1")), "ai_model": "mock",
                "ai_summary_prompt": "只输出三条要点，不使用表格。",
            })
            payload = {"project_ids": [project["id"]], "date": TODAY, "history_days": 0}
            first = await self.post("ai/summary", payload)
            self.assertTrue(first["persisted"])
            self.assertEqual(received[0]["messages"][0]["role"], "system")
            self.assertEqual(received[0]["messages"][1]["role"], "user")
            self.assertIn("只输出三条要点，不使用表格。", received[0]["messages"][0]["content"])

            updated = "只输出两列表格：项目、实际工作。"
            await self.post("settings/save", {"ai_summary_prompt": updated})
            self.assertEqual(Settings(self.root / "settings.json")["ai_summary_prompt"], updated)
            preview = await self.post("ai/summary", payload)
            self.assertFalse(preview["persisted"])
            sent = received[1]["messages"][0]["content"]
            self.assertIn(updated, sent)
            self.assertNotIn("只输出三条要点", sent)
            self.assertIn("资料解读规则", sent)
            response = await self.client.get(f"/api/summaries?start={TODAY}&end={TODAY}")
            self.assertEqual((await response.json())["data"]["summaries"][0]["content"], "结果1")

            await self.post("settings/test", {})
            self.assertEqual(received[2]["messages"][0]["content"], "请只回复：连接成功")
        finally:
            await server.close()

    async def test_summary_preview_snapshot_and_weekly_range(self):
        with patch("ppm.database._now", return_value="2026-09-21T10:00:00"):
            project = await self.post("projects/save", {"name": "周报项目"})
        await self.post("settings/save", {"ai_model": "test", "ai_api_key": "never-in-preview"})
        payload = {"project_ids": [project["id"]], "date": "2026-09-28", "period": "weekly", "template": "weekly"}
        await self.post("worklogs/save", {"project_id": project["id"], "work_date": "2026-09-22", "content": "范围内的真实工作"})
        await self.post("worklogs/save", {"project_id": project["id"], "work_date": "2026-09-21", "content": "范围外的旧记录"})
        preview = await self.post("ai/preview", payload)
        self.assertEqual(preview["start"], "2026-09-22")
        self.assertIn("范围内的真实工作", preview["material"])
        self.assertNotIn("范围外的旧记录", preview["material"])
        self.assertNotIn("never-in-preview", json.dumps(preview))
        with patch("ppm.llm.ModelClient.generate", AsyncMock(return_value="周报")) as model:
            result = await self.post("ai/summary", {**payload, "fingerprint": preview["fingerprint"], "overwrite": True})
            self.assertFalse(result["persisted"])
            self.assertEqual(model.call_args.args[0], preview["request"]["messages"][1]["content"])
            self.assertEqual(model.call_args.kwargs["system_prompt"], preview["request"]["messages"][0]["content"])
            await self.post("settings/save", {"ai_model": "changed"})
            response = await self.client.post("/api/ai/summary", json={**payload, "fingerprint": preview["fingerprint"]})
            self.assertEqual(response.status, 400)
            self.assertEqual(model.await_count, 1)
        response = await self.client.get("/api/summaries?start=2026-09-22&end=2026-09-28")
        self.assertEqual((await response.json())["data"]["summaries"], [])

    async def test_diagnostics_independent_calls_and_redaction(self):
        received = []
        async def models(request):
            received.append("models")
            return web.json_response({"data": [{"id": "test"}]}, headers={"X-Request-Id": "list-id"})
        async def complete(request):
            received.append(await request.json())
            return web.json_response({"error": {"message": "sk-private-key denied", "code": "RateLimitExceeded.EndpointRPMExceeded"}},
                status=429, headers={"X-Request-Id": "request-id", "Retry-After": "5"})
        app = web.Application()
        app.router.add_get("/v1/models", models)
        app.router.add_post("/v1/chat/completions", complete)
        server = TestServer(app)
        await server.start_server()
        try:
            await self.post("settings/save", {"ai_base_url": str(server.make_url("/v1")), "ai_model": "test", "ai_api_key": "sk-private-key"})
            result = await self.post("settings/diagnose", {"kind": "models"})
            self.assertTrue(result["ok"])
            self.assertTrue(result["selected_model_listed"])
            self.assertEqual(received, ["models"])
            result = await self.post("settings/diagnose", {"kind": "generation"})
            self.assertFalse(result["ok"])
            self.assertEqual(result["http_status"], 429)
            self.assertEqual(result["request_id"], "request-id")
            self.assertEqual(result["retry_after"], "5")
            self.assertEqual(result["error_code"], "RateLimitExceeded.EndpointRPMExceeded")
            self.assertGreaterEqual(result["elapsed_ms"], 0)
            self.assertNotIn("sk-private-key", json.dumps(result))
            self.assertEqual(result["request"]["body"], received[1])
            self.assertEqual(len(received), 2)
        finally:
            await server.close()

    async def test_all_post_routes_reject_invalid_json(self):
        paths = {r.resource.canonical for r in self.client.server.app.router.routes() if r.method == "POST"}
        for path in paths:
            for payload in (None, [], "text", 123):
                response = await self.client.post(path, data=json.dumps(payload), headers={"Content-Type":"application/json"})
                self.assertEqual(response.status, 400, (path,payload))
            response = await self.client.post(path, data="{broken", headers={"Content-Type":"application/json"})
            self.assertEqual(response.status, 400, path)

    async def test_settings_secret_persistence_and_validation(self):
        await self.post("settings/save", {"ai_api_key":"hidden-key", "ai_model":"local", "ui_color_theme":"ocean", "ai_timeout":120})
        result = await self.post("settings/save", {"week_start":"sunday"})
        self.assertEqual(result["ai_api_key"], "hidden-key")
        self.assertTrue(result["ai_key_configured"])
        restored = Settings(self.root / "settings.json")
        self.assertEqual(restored["ai_api_key"], "hidden-key")
        self.assertEqual(restored["ui_color_theme"], "ocean")
        for data in ({"ai_timeout":True},{"ai_base_url":"file:///tmp"},{"ui_color_theme":"wrong"}):
            self.assertEqual((await self.client.post("/api/settings/save", json=data)).status,400)
        result = await self.post("settings/save", {"clear_api_key":True})
        self.assertFalse(result["ai_key_configured"])

    async def test_access_and_cross_site_protection(self):
        response = await self.client.post("/api/members/save", json={"name":"attack"},headers={"Origin":"https://elsewhere.example"})
        self.assertEqual(response.status,403)
        self.assertEqual((await self.client.post("/api/members/save", data="name=attack")).status,415)
        secured = TestClient(TestServer(create_app(self.root, password="password")))
        await secured.start_server()
        try:
            self.assertEqual((await secured.get("/")).status,401)
            self.assertEqual((await secured.get("/healthz")).status,200)
            auth = "Basic " + base64.b64encode(b"admin:password").decode()
            self.assertEqual((await secured.get("/",headers={"Authorization":auth})).status,200)
        finally:
            await secured.close()

    async def test_model_http_transport(self):
        received=[]
        async def complete(request):
            received.append((request.headers.get("Authorization"),await request.json()))
            return web.json_response({"choices":[{"message":{"content":"  模型回复  "}}]})
        app=web.Application(); app.router.add_post("/v1/chat/completions", complete)
        server=TestServer(app); await server.start_server()
        try:
            settings=Settings(self.root/"model.json")
            settings.save({"ai_base_url":str(server.make_url("/v1")),"ai_model":"example","ai_api_key":"key"})
            result=await ModelClient(settings).generate("资料")
            self.assertEqual(result,"模型回复")
            self.assertEqual(received[0][0],"Bearer key")
            self.assertEqual(received[0][1]["messages"][0]["content"],"资料")
        finally:
            await server.close()

    async def test_model_discovery_draft_saved_key_and_no_write(self):
        received = []
        async def models(request):
            received.append(request.headers.get("Authorization"))
            return web.json_response({"data":[{"id":"z-chat"},{"id":"a-chat"},{"id":"z-chat"}]})
        app=web.Application(); app.router.add_get("/v1/models",models)
        server=TestServer(app); await server.start_server()
        try:
            url=str(server.make_url("/v1"))
            result=await self.post("settings/models", {"ai_base_url":url,"ai_api_key":"draft-key"})
            self.assertEqual(result["models"],["a-chat","z-chat"])
            self.assertFalse((self.root/"settings.json").exists())
            await self.post("settings/save", {"ai_base_url":url,"ai_api_key":"saved-key"})
            result=await self.post("settings/models", {})
            self.assertEqual(received,["Bearer draft-key","Bearer saved-key"])
            self.assertNotIn("saved-key",str(result))
            await self.post("settings/models", {"clear_api_key":True})
            self.assertIsNone(received[-1])
            self.assertEqual(Settings(self.root/"settings.json")["ai_api_key"],"saved-key")
            denied=await self.client.post("/api/settings/models",json={"ai_base_url":url+"/other"})
            self.assertEqual(denied.status,400)
            self.assertEqual(len(received),3)
        finally: await server.close()

    async def test_model_discovery_errors_empty_and_redirect(self):
        result = {"status":200,"body":{"data":[]}}
        async def models(request):
            return web.json_response(result["body"], status=result["status"], headers={"Location":"/should-not-follow"})
        app=web.Application(); app.router.add_get("/v1/models",models)
        server=TestServer(app); await server.start_server()
        try:
            url=str(server.make_url("/v1"))
            self.assertEqual((await self.post("settings/models",{"ai_base_url":url}))["models"],[])
            for status,body in [(401,{"error":"private upstream details"}),(404,{}),(302,{}),(200,[]),(200,{"data":[{}]})]:
                result.update(status=status,body=body)
                response=await self.client.post("/api/settings/models",json={"ai_base_url":url})
                self.assertEqual(response.status,400)
                message=(await response.json())["error"]
                self.assertNotIn("private upstream details",message)
                if status==404: self.assertIn("不支持",message)
                if status==401: self.assertIn("401",message)
        finally: await server.close()

    async def test_provider_rate_limit_diagnostics_redact_key(self):
        async def limited(request):
            return web.json_response({"error":{"message":"inference exceeds tpm/rpm limit secret-test-key Bearer other-key sk-example123", "code":"RateLimitExceeded.EndpointRPMExceeded"}},status=429,headers={"X-Request-Id":"trace-123","Retry-After":"60"})
        app=web.Application();app.router.add_post("/v1/chat/completions",limited)
        server=TestServer(app);await server.start_server()
        try:
            config=Settings(self.root/"diagnostic.json")
            config.save({"ai_base_url":str(server.make_url("/v1")),"ai_model":"test","ai_api_key":"secret-test-key"})
            with self.assertRaises(ValidationError) as error: await ModelClient(config).generate("test")
            message=str(error.exception)
            for expected in ("HTTP 429","RPM","EndpointRPMExceeded","trace-123","60","inference exceeds"):
                self.assertIn(expected,message)
            for secret in ("secret-test-key","other-key","sk-example123"):
                self.assertNotIn(secret,message)
        finally: await server.close()

    async def test_todo_crud_and_scoped_settings(self):
        project=await self.post("projects/save",{"name":"待办归属"})
        todo=await self.post("todos/save",{"project_id":project["id"],"content":"待确认方案"})
        self.assertEqual(todo["status"],"未完成")
        completed=await self.post("todos/save",{"id":todo["id"],"status":"已完成"})
        self.assertTrue(completed["completed_at"])
        self.assertEqual(completed["content"],"待确认方案")
        reopened=await self.post("todos/save",{"id":todo["id"],"status":"未完成","project_id":None})
        self.assertIsNone(reopened["completed_at"])
        self.assertIsNone(reopened["project_id"])
        self.assertEqual((await self.client.get("/api/todos")).status,200)
        await self.post("todos/delete",{"id":todo["id"]})
        self.assertEqual((await (await self.client.get("/api/todos")).json())["data"],[])
        response=await self.client.post("/api/todos/save",json={"id":todo["id"],"status":"已完成"})
        self.assertEqual(response.status,404)
        await self.post("settings/save",{"ai_api_key":"visible-key","ai_model":"chat-a","ai_models":["chat-b"]})
        await self.post("settings/save",{"ui_color_theme":"amber"})
        config=(await (await self.client.get("/api/settings")).json())["data"]
        self.assertEqual(config["ai_api_key"],"visible-key")
        self.assertEqual(config["ai_models"],["chat-b","chat-a"])
        await self.post("settings/save",{"ai_api_key":""})
        self.assertEqual(Settings(self.root/"settings.json")["ai_api_key"],"")

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def legacy(self):
        path=self.root/"legacy"/"ppm.sqlite3"
        db=Database(path); db.initialize()
        project=db.save_project({"name":"原有项目"})
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE todos(id INTEGER PRIMARY KEY, content TEXT, push_enabled INTEGER)")
            conn.execute("INSERT INTO todos VALUES(1,'旧待办',1)")
            conn.execute("PRAGMA user_version=3")
        return path, project
    def test_import_preserves_source_and_archives_todos(self):
        source,project=self.legacy()
        target=import_database(source,self.root/"new")
        db=prepare_data(target.parent)
        self.assertEqual(db.list_projects()[0]["id"],project["id"])
        with closing(sqlite3.connect(target)) as conn, conn:
            self.assertEqual(conn.execute("SELECT content FROM legacy_todos").fetchone()[0],"旧待办")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],5)
        with closing(sqlite3.connect(source)) as conn, conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],3)
        self.assertEqual(len(list(target.parent.glob("backups/*.sqlite3"))),1)
        with self.assertRaises(ValueError): import_database(source,target.parent)
        prepare_data(target.parent)
        self.assertEqual(len(list(target.parent.glob("backups/*.sqlite3"))),1)
    def test_upgrade_backup_and_future_version_rejection(self):
        source,_=self.legacy()
        prepare_data(source.parent)
        self.assertEqual(len(list(source.parent.glob("backups/*.sqlite3"))),1)
        with closing(sqlite3.connect(source)) as conn, conn: conn.execute("PRAGMA user_version=99")
        with self.assertRaises(ValueError): prepare_data(source.parent)
    def test_reject_unrelated_database(self):
        path=self.root/"unrelated.sqlite3"
        with closing(sqlite3.connect(path)) as conn, conn: conn.execute("CREATE TABLE unrelated(x)")
        with self.assertRaises(ValueError): import_database(path,self.root/"new")

if __name__ == "__main__": unittest.main()
