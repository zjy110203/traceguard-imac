"""HttpObserver：基于标准库 urllib 的确定性 HTTP 观测（降级路径，明确标记）。

- 真实 HTTP 请求到 TeamFlow，不做 Mock；
- 解析 HTML（正则/字符串，足够覆盖 TeamFlow 的 data-testid 页面）；
- 支持 navigate/fill(记录)/click(api)/assert 与 api 步骤；
- mode="http-fallback"，报告中明确标注非真实浏览器渲染。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from traceguard_contracts.models import TestStep

from .observer import ObservationRecord, Observer


class HttpObserver(Observer):
    mode = "http-fallback"

    def __init__(self) -> None:
        self.base_url = "http://127.0.0.1:8011"
        self._cookies: dict[str, str] = {}
        self._last_page_html = ""
        self._fields: dict[str, str] = {}

    def setup(self, run_config: dict[str, Any]) -> None:
        self.base_url = run_config.get("base_url", self.base_url).rstrip("/")

    def teardown(self) -> None:
        self._fields.clear()

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, str, str]:
        url = path if path.startswith("http") else self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return (
                    resp.status,
                    resp.read().decode("utf-8", "replace"),
                    dict(resp.headers).get("Content-Type", ""),
                )
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace"), ""

    def _element_text(self, test_id: str) -> str | None:
        """从 HTML 中提取 data-testid=<id> 元素的文本内容（轻量解析）。"""
        m = re.search(
            rf'<[^>]*data-testid="{re.escape(test_id)}"[^>]*>(.*?)</[^>]+>',
            self._last_page_html,
            re.DOTALL,
        )
        return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else None

    def observe(self, step: TestStep, context: dict[str, Any]) -> ObservationRecord:
        action = step.action
        obs: dict[str, Any] = {}
        try:
            if action == "navigate":
                status, html, _ = self._request("GET", step.value or "/")
                self._last_page_html = html
                obs = {"status": status, "url": step.value}
                # 提取页面上关键元素的文本（断言用）
                for tid in ("banner-success", "banner-error", "create-error", "login-message"):
                    t = self._element_text(tid)
                    if t is not None:
                        obs[tid] = t

            elif action == "fill":
                tid = step.target.value if step.target else ""
                self._fields[tid] = step.value or ""
                obs = {"filled": tid}

            elif action == "click":
                tid = step.target.value if step.target else ""
                # 登录/创建：根据已填字段发 API 请求
                if tid == "login-submit":
                    status, body, _ = self._request(
                        "POST",
                        "/api/auth/login",
                        {
                            "username": self._fields.get("username", ""),
                            "password": self._fields.get("password", ""),
                        },
                    )
                    obs = {"status": status, "response": _safe_json(body)}
                    self._last_page_html = (
                        f'<div data-testid="login-message">{_login_msg(status)}</div>'
                    )
                elif tid == "create-submit":
                    status, body, _ = self._request(
                        "POST",
                        "/api/projects",
                        {
                            "name": self._fields.get("project-name", ""),
                            "description": self._fields.get("project-description", ""),
                            "workspace_slug": "default",
                        },
                    )
                    obs = {"status": status, "response": _safe_json(body)}
                    if status == 200:
                        self._last_page_html = (
                            '<div data-testid="banner-success">项目创建成功</div>'
                        )
                    else:
                        detail = _safe_json(body).get("detail", "创建失败")
                        self._last_page_html = (
                            f'<div data-testid="create-error">创建失败：{detail}</div>'
                        )
                else:
                    obs = {"clicked": tid}

            elif action == "api":
                method, path = _parse_api_step(step.value or "")
                body = context.get("api_body")
                status, resp, _ = self._request(method, path, body)
                obs = {"status": status, "json": _safe_json(resp)}

            elif action == "assert":
                # 断言步骤本身：收集当前已知元素文本
                obs = {"text": self._last_page_html}
                for tid in ("banner-success", "banner-error", "create-error", "login-message"):
                    t = self._element_text(tid)
                    if t is not None:
                        obs[tid] = t
                obs["text"] = re.sub(r"<[^>]+>", " ", self._last_page_html)
                # 从最近一次业务状态查询取 count（若有）
                if "state_count" in context:
                    obs["count"] = context["state_count"]

            elif action == "wait":
                obs = {"waited": True}

            else:
                obs = {"unsupported_action": action}

            return ObservationRecord(step_id=step.step_id, action=action, observation=obs)
        except Exception as e:  # noqa: BLE001
            return ObservationRecord(
                step_id=step.step_id,
                action=action,
                observation={"timed_out": isinstance(e, TimeoutError), "error": str(e)},
                error=str(e),
            )


def _safe_json(text: str) -> dict[str, Any]:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {"value": v}
    except Exception:  # noqa: BLE001
        return {"raw": text[:200]}


def _login_msg(status: int) -> str:
    return "登录成功" if status == 200 else "登录失败：用户名或密码错误"


def _parse_api_step(value: str) -> tuple[str, str]:
    parts = value.strip().split(" ", 1)
    if len(parts) == 2 and parts[0].upper() in ("GET", "POST", "PUT", "DELETE"):
        return parts[0].upper(), parts[1]
    return "GET", value
