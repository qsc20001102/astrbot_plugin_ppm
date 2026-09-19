import asyncio
import importlib.util
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ppm.errors import NotFoundError, ValidationError


def load_web_api():
    api = ModuleType("astrbot.api")
    api.logger = Mock()
    web = ModuleType("astrbot.api.web")
    web.request = SimpleNamespace(json=AsyncMock(return_value={}), query={})
    web.json_response = lambda data: {"data": data, "status": 200}
    web.error_response = lambda message, status_code: {"error": message, "status": status_code}
    spec = importlib.util.spec_from_file_location("ppm._web_api_test", Path(__file__).parents[1] / "ppm/web_api.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", {"astrbot": ModuleType("astrbot"), "astrbot.api": api, "astrbot.api.web": web}):
        spec.loader.exec_module(module)
    return module


module = load_web_api()


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Mock()
        self.context = Mock()
        self.api = module.WebApi(self.db, self.context, {"ai_provider_id": "model"})
        module.request.json = AsyncMock(return_value={})

    async def test_database_operation_runs_off_event_loop_thread(self):
        main_thread = threading.get_ident()
        response = await self.api._run(threading.get_ident)
        self.assertNotEqual(response["data"], main_thread)

    async def test_error_statuses(self):
        for error, status in ((ValidationError("bad"), 400), (NotFoundError("missing"), 404), (RuntimeError("failed"), 500)):
            with self.subTest(status=status):
                result = await self.api._run(Mock(side_effect=error))
                self.assertEqual(result["status"], status)

    async def test_all_post_routes_reject_non_object_payload(self):
        self.api.register("ppm")
        for call in self.context.register_web_api.call_args_list:
            path, handler, methods, _ = call.args
            if "POST" not in methods:
                continue
            for value in ([], None, "text", 123):
                with self.subTest(path=path, value=value):
                    module.request.json.return_value = value
                    response = await handler()
                    self.assertEqual(response["status"], 400)

    async def test_empty_model_response_does_not_save(self):
        self.db.team_summary_material.return_value = ([1], "material")
        for value in (None, "", "  ", [], {}):
            self.context.llm_generate = AsyncMock(return_value=SimpleNamespace(completion_text=value))
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                await self.api.generate_summary([1], "2026-01-01")
        self.db.save_team_summary.assert_not_called()

    async def test_summary_persists_generated_content(self):
        self.db.team_summary_material.return_value = ([1], "material")
        self.db.save_team_summary.return_value = True
        self.context.llm_generate = AsyncMock(return_value=SimpleNamespace(completion_text=" result "))
        result = await self.api.generate_summary([1], "2026-01-01", overwrite=True)
        self.assertEqual(result["content"], "result")
        self.assertTrue(result["persisted"])
        self.db.save_team_summary.assert_called_once_with([1], "2026-01-01", "model", "result", overwrite=True)

    async def test_health_only_exposes_display_settings(self):
        self.api.config.update(company_name="测试团队", week_start="sunday", secret="private")
        result = await self.api.health()
        self.assertEqual(result["data"]["settings"], {"company_name": "测试团队", "week_start": "sunday"})
        self.assertNotIn("secret", str(result))


if __name__ == "__main__":
    unittest.main()
