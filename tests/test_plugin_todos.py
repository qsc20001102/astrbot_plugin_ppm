import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class PluginTodoLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_wires_start_send_and_stop(self):
        root = Path(__file__).parents[1]
        package = ModuleType("_ppm_plugin_test")
        package.__path__ = [str(root)]
        api = ModuleType("astrbot.api")
        api.AstrBotConfig, api.logger = dict, Mock()
        events = ModuleType("astrbot.api.event")
        events.AstrMessageEvent = object
        events.filter = SimpleNamespace(command=lambda _: lambda handler: handler)
        chain = Mock()
        events.MessageChain = Mock(return_value=chain)
        stars = ModuleType("astrbot.api.star")

        class Star:
            def __init__(self, context):
                self.context = context

        stars.Star, stars.Context = Star, object
        paths = ModuleType("astrbot.core.utils.astrbot_path")
        web = ModuleType("astrbot.api.web")
        web.request, web.json_response, web.error_response = Mock(), Mock(), Mock()
        with tempfile.TemporaryDirectory() as scratch:
            paths.get_astrbot_data_path = lambda: scratch
            modules = {"_ppm_plugin_test": package, "astrbot": ModuleType("astrbot"),
                "astrbot.api": api, "astrbot.api.event": events, "astrbot.api.star": stars,
                "astrbot.api.web": web, "astrbot.core.utils.astrbot_path": paths}
            with patch.dict("sys.modules", modules):
                spec = importlib.util.spec_from_file_location("_ppm_plugin_test.main", root / "main.py")
                main = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(main)
                context = Mock()
                context.send_message = AsyncMock(return_value=True)
                plugin = main.PPMPlugin(context, {})
                await plugin.initialize()
                self.assertIsNotNone(plugin.todo_reminders._task)
                self.assertTrue(await plugin._send_todo_reminder("test:GroupMessage:1", "待办测试"))
                chain.message.assert_called_once_with("待办测试")
                context.send_message.assert_awaited_once_with("test:GroupMessage:1", chain.message.return_value)
                await plugin.terminate()
                self.assertIsNone(plugin.todo_reminders._task)
