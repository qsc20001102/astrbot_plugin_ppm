import asyncio
import json
import re
from aiohttp import ClientSession, ClientTimeout, ClientError
from .errors import ValidationError


async def provider_error(response, api_key=""):
    """Expose bounded provider diagnostics without reflecting credentials or HTML."""
    def safe(value):
        text = str(value) if isinstance(value, (str, int)) else ""
        if api_key:
            text = text.replace(api_key, "[密钥已隐藏]")
        text = re.sub(r"(?i)Bearer\s+[^\s\"',;]+", "Bearer [已隐藏]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[密钥已隐藏]", text)
        return " ".join(text.split())[:500]

    message, code = "", ""
    try:
        raw = await response.content.read(8192)
        body = json.loads(raw)
        error = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(error, dict):
            message, code = safe(error.get("message")), safe(error.get("code") or error.get("type"))
    except (ValueError, UnicodeError):
        pass
    status = response.status
    if status == 429:
        hint = "服务商限流，请稍后重试或检查该模型的限流配额"
        if "TPMExceeded" in code:
            hint = "服务商限制了每分钟 Token 吞吐量（TPM），请稍后重试或联系服务商核对限额"
        elif "RPMExceeded" in code:
            hint = "服务商限制了每分钟请求次数（RPM），请稍后重试或联系服务商核对限额"
    elif status in (401, 403):
        hint = "认证或访问权限失败，请检查密钥及模型权限"
    else:
        hint = "模型服务请求失败"
    parts = [f"{hint}（HTTP {status}）"]
    if code:
        parts.append(f"错误码：{code}")
    if message:
        parts.append(f"服务商说明：{message}")
    request_id = safe(response.headers.get("X-Request-Id") or response.headers.get("Request-Id"))
    if request_id:
        parts.append(f"请求 ID：{request_id}")
    retry_after = safe(response.headers.get("Retry-After"))
    if retry_after:
        parts.append(f"Retry-After：{retry_after}")
    error = ValidationError("；".join(parts))
    error.provider_code = code
    return error

class ModelClient:
    """OpenAI-compatible Chat Completions transport, including local endpoints."""
    def __init__(self, settings):
        self.settings = settings

    async def list_models(self, *, diagnostics=None):
        config = dict(self.settings)
        headers = {}
        if config.get("ai_api_key"):
            headers["Authorization"] = "Bearer " + config["ai_api_key"]
        try:
            async with ClientSession(timeout=ClientTimeout(total=min(config["ai_timeout"], 30))) as session:
                async with session.get(config["ai_base_url"].rstrip("/") + "/models",
                    headers=headers, allow_redirects=False) as response:
                    self._record_response(response, diagnostics)
                    if response.status in (404, 405):
                        raise ValidationError("服务商不支持获取模型列表，请手动填写模型名称")
                    if response.status != 200:
                        raise await provider_error(response, config.get("ai_api_key", ""))
                    data = await response.json()
            if not isinstance(data, dict) or not isinstance(data.get("data"), list):
                raise ValueError()
            models = sorted({item["id"].strip() for item in data["data"]
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()})
            if data["data"] and not models:
                raise ValueError()
            return models
        except ValidationError:
            raise
        except (asyncio.TimeoutError, ClientError) as exc:
            raise ValidationError("获取模型列表失败或超时，请检查地址和网络；也可手动填写模型名称") from exc
        except (ValueError, TypeError) as exc:
            raise ValidationError("模型列表格式不兼容，请手动填写模型名称") from exc

    async def generate(self, prompt, *, system_prompt=None, diagnostics=None):
        config = dict(self.settings)
        if not config.get("ai_model"):
            raise ValidationError("请先在模型配置中填写模型名称并保存")
        headers = {}
        if config.get("ai_api_key"):
            headers["Authorization"] = "Bearer " + config["ai_api_key"]
        try:
            async with ClientSession(timeout=ClientTimeout(total=config["ai_timeout"])) as session:
                async with session.post(config["ai_base_url"].rstrip("/") + "/chat/completions",
                    headers=headers, json=self.completion_body(config, prompt, system_prompt),
                    allow_redirects=False) as response:
                    self._record_response(response, diagnostics)
                    if response.status != 200:
                        raise await provider_error(response, config.get("ai_api_key", ""))
                    data = await response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            return content.strip()
        except ValidationError:
            raise
        except (asyncio.TimeoutError, ClientError) as exc:
            raise ValidationError("模型连接失败或超时，请检查地址和网络") from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValidationError("模型未返回有效文本，请确认支持 Chat Completions 接口") from exc

    @staticmethod
    def completion_body(config, prompt, system_prompt=None):
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return {"model": config["ai_model"], "messages": messages, "stream": False}

    @staticmethod
    def _record_response(response, diagnostics):
        if diagnostics is not None:
            diagnostics.update({"http_status": response.status,
                "request_id": response.headers.get("X-Request-Id") or response.headers.get("Request-Id"),
                "retry_after": response.headers.get("Retry-After")})
            diagnostics["request"]["headers"] = {
                name: ("Bearer [已隐藏]" if name.lower() == "authorization" else value)
                for name, value in response.request_info.headers.items()
                if name.lower() in {"authorization", "content-type", "content-length", "accept", "accept-encoding", "user-agent"}
            }
