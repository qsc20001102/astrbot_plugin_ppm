import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from .errors import ValidationError

DEFAULTS = {
    "ai_base_url": "https://api.openai.com/v1", "ai_model": "", "ai_api_key": "",
    "ai_timeout": 120, "ai_summary_prompt": "用简洁中文按项目、任务总结当天实际工作，不虚构成果、风险或计划。",
    "week_start": "monday", "ui_color_theme": "forest",
    "ai_models": [],
}

class Settings(dict):
    def __init__(self, path):
        self.path = Path(path)
        super().__init__(DEFAULTS)
        if self.path.exists():
            self.update(json.loads(self.path.read_text(encoding="utf-8")))

    def public(self):
        return {**self,
                "ai_key_configured": bool(self.get("ai_api_key"))}

    def resolve(self, payload):
        """Validate proposed settings without saving or changing the live config."""
        values = dict(self)
        for key in DEFAULTS:
            if key in payload:
                value = payload[key]
                if key == "ai_models":
                    if not isinstance(value, list) or len(value) > 200 or any(not isinstance(v, str) or not v.strip() or len(v) > 300 for v in value):
                        raise ValidationError("模型列表最多支持 200 个有效模型名称")
                    value = list(dict.fromkeys(v.strip() for v in value))
                elif key == "ai_timeout":
                    if type(value) is not int or not 10 <= value <= 600:
                        raise ValidationError("超时必须是 10–600 秒的整数")
                elif not isinstance(value, str) or len(value) > 16000:
                    raise ValidationError(f"无效的设置：{key}")
                values[key] = value.strip() if isinstance(value, str) and key != "ai_summary_prompt" else value
        if payload.get("clear_api_key") is True:
            values["ai_api_key"] = ""
        url = urlsplit(values["ai_base_url"])
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValidationError("API 地址必须是完整 HTTP(S) 基础地址，不含密钥或查询参数")
        if values["week_start"] not in ("monday", "sunday") or values["ui_color_theme"] not in ("forest", "ocean", "violet", "amber"):
            raise ValidationError("无效的显示设置")
        if values["ai_model"] and values["ai_model"] not in values["ai_models"]:
            values["ai_models"] = [*values["ai_models"], values["ai_model"]]
        return values

    def save(self, payload):
        values = self.resolve(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temp, 0o600)
        os.replace(temp, self.path)
        self.clear()
        self.update(values)
        return self.public()
