"""RUN-017 WP2.5：浏览器级 UI / Locator 故障效果独立观测（Browser Effect Observer）。

解决的问题：`TF-UI-*` / `TF-LOC-*` / `TF-COMP-003` 在 RUN-016 只能给 `UNKNOWN`，
因为没有一个"不依赖被测用例"的观测者。本模块用**独立 Playwright BrowserContext**
执行一次真实用户操作，回答：

- 故障是否真正改变了页面行为？
- 用户可见功能是否受到影响？
- 是否只是测试定位器（test_id）变了，而用户仍能完成业务？
- 是否造成业务操作失败（独立业务状态回读为证）？
- 是否只是证据不足？

隔离要求（硬约束）：

- 不读取 Run Verdict / Ground Truth / 诊断结果 / 模型预测——构造函数不接受这些参数；
- 不复用被测用例的 test_id / CSS 定位器：操作一律用 role / accessible name / label；
- 独立 BrowserContext + 独立会话（不复用被测 Run 的 Cookie/存储）；
- 业务结果以 TeamFlow 权威状态接口回读为准，不以 Toast/文案作为成功证据。

回读三态（RUN-018 WP2）：业务记录是否存在只允许三态判定——HTTP 200 + ok=true +
合法 projects 列表才允许判 PRESENT / ABSENT；状态接口 500 / 403 / 连接失败 / 超时 /
非法 JSON / 缺少必要字段一律 UNKNOWN 并给出观测失败原因。UNKNOWN 时分类器不得进入
MISLEADING_SUCCESS_ON_FAILURE / SILENT_IGNORE / OPERATION_FAILED_ERROR_SURFACED
（读取失败 ≠ 业务没有落库）。截图 / DOM / 网络与清理记录在任何路径下都保留。

注意：test_id 只用于**观测"定位器资产是否变化"**（这是待回答的问题之一），
不用于定位控件。所有点击/输入都走 `get_by_role` / `get_by_label`。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fault_effect import EffectStatus, ReadVerdict, network_error_reason, new_probe_token

#: UI 表单固定写入 default workspace（产品行为，不为测试改动）
UI_WORKSPACE = "default"
DEFAULT_WORKSPACE = "default"

#: 期望存在的测试定位器（仅用于"定位器资产是否变化"的观测）
EXPECTED_TEST_IDS = ("project-name", "create-submit")


class BrowserUnavailable(RuntimeError):
    """Playwright / Chromium 不可用：观测无法进行（不得降级成 UNKNOWN 结论）。"""


@dataclass
class BrowserObservation:
    status: str
    classification: str
    detail: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "classification": self.classification,
            "detail": self.detail,
            "evidence_refs": self.evidence_refs,
            "artifacts": self.artifacts,
        }


def _request_json(
    method: str,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """最小 HTTP 客户端：完整保留 HTTP 状态码 / 原始响应 / 失败原因（RUN-018 WP2）。

    返回：`http_status`（0 = 没拿到 HTTP 响应）、`payload`（合法 JSON 对象，否则 None）、
    `raw`、`error`（网络错误归类 / HTTP_x / NON_JSON_RESPONSE / ""）、`elapsed_ms`。
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method=method,
    )
    started = time.perf_counter()
    raw = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 受控内网
            status = resp.status
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # HTTP 错误也是真实状态码：必须如实记录（旧实现会吞成 status=0）
        return {
            "http_status": exc.code,
            "payload": None,
            "raw": exc.read(8192).decode("utf-8", "replace")[:2000],
            "error": f"HTTP_{exc.code}",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 - 观测失败要如实返回
        return {
            "http_status": 0,
            "payload": None,
            "raw": "",
            "error": network_error_reason(str(exc)),
            "error_detail": f"{type(exc).__name__}: {exc}"[:300],
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {
            "http_status": status,
            "payload": None,
            "raw": raw[:2000],
            "error": "NON_JSON_RESPONSE",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    if not isinstance(payload, dict):
        return {
            "http_status": status,
            "payload": None,
            "raw": raw[:2000],
            "error": "NON_OBJECT_JSON",
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    return {
        "http_status": status,
        "payload": payload,
        "raw": raw[:2000],
        "error": "",
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


class BrowserEffectObserver:
    """独立浏览器观测者：一次观测 = 一次真实的"新建项目"用户操作。"""

    def __init__(
        self,
        base_url: str,
        *,
        out_dir: str | Path | None = None,
        headless: bool = True,
        action_timeout_ms: int = 15000,
        settle_timeout_ms: int = 12000,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.out_dir = Path(out_dir) if out_dir else None
        self.headless = headless
        self.action_timeout_ms = action_timeout_ms
        self.settle_timeout_ms = settle_timeout_ms

    # ------------------------------------------------------------------ public
    def observe(self, fault_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """执行一次独立观测，返回可直接写入样本的字段集合。"""
        from playwright.sync_api import sync_playwright  # 延迟导入：无浏览器时不影响其它探针

        faults = [str(f) for f in fault_ids]
        token = new_probe_token()
        name = f"bp:{token}:ui"
        started = time.perf_counter()
        self._ensure_out_dir()
        observation: BrowserObservation
        try:
            with sync_playwright() as pw:
                try:
                    browser = pw.chromium.launch(headless=self.headless)
                except Exception as exc:  # noqa: BLE001 - 浏览器不可用必须显式失败
                    raise BrowserUnavailable(
                        f"Chromium 启动失败：{type(exc).__name__}: {exc}"
                    ) from exc
                try:
                    observation = self._run(browser, faults, name, token, started)
                finally:
                    browser.close()
        except BrowserUnavailable as exc:
            return {
                "fault_effect_status": EffectStatus.UNKNOWN.value,
                "fault_effect_source": "BROWSER_EFFECT_OBSERVER:UNAVAILABLE",
                "fault_effect_evidence_refs": [],
                "fault_effect_probes": [
                    {
                        "kind": "browser",
                        "impacted": None,
                        "classification": "BROWSER_UNAVAILABLE",
                        "detail": {"error": str(exc)[:300]},
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                ],
                "fault_effect_classification": "BROWSER_UNAVAILABLE",
                "fault_effect_note": "浏览器不可用，未能完成独立观测（不得记为无影响）",
                "fault_effect_requires_environment_reset": False,
                "benchmark_schema_version": "benchmark-fault-v2",
            }
        payload = observation.to_dict()
        return {
            "fault_effect_status": observation.status,
            "fault_effect_source": "BROWSER_EFFECT_OBSERVER:role_name_flow",
            "fault_effect_evidence_refs": observation.evidence_refs,
            "fault_effect_probes": [
                {
                    "kind": "browser",
                    "impacted": observation.status == EffectStatus.CONFIRMED.value,
                    "classification": observation.classification,
                    "detail": payload["detail"],
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            ],
            "fault_effect_classification": observation.classification,
            "fault_effect_note": payload["detail"].get("note", ""),
            "fault_effect_requires_environment_reset": bool(
                payload["detail"].get("requires_environment_reset")
            ),
            "fault_effect_artifacts": observation.artifacts,
            "benchmark_schema_version": "benchmark-fault-v2",
        }

    # ----------------------------------------------------------------- private
    def _ensure_out_dir(self) -> None:
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)

    def _artifact(self, suffix: str, text: str) -> str:
        if not self.out_dir:
            return ""
        path = self.out_dir / f"observation-{int(time.time() * 1000)}{suffix}"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def _run(
        self,
        browser: Any,
        faults: list[str],
        name: str,
        token: str,
        started: float,
    ) -> BrowserObservation:
        detail: dict[str, Any] = {
            "flow": "open /projects/new → fill 项目名称 → click 创建 → read /projects banner",
            "name": name,
            "probe_token": token,
            "faults": faults,
        }
        artifacts: list[str] = []
        evidence: list[str] = []
        console_errors: list[dict[str, Any]] = []
        responses: list[dict[str, Any]] = []
        failed_requests: list[dict[str, Any]] = []
        nav: list[dict[str, Any]] = []

        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.set_default_timeout(self.action_timeout_ms)
        page.on(
            "console",
            lambda msg: (
                console_errors.append({"type": msg.type, "text": msg.text[:300]})
                if msg.type == "error"
                else None
            ),
        )
        page.on(
            "response",
            lambda resp: responses.append(
                {"status": resp.status, "url": resp.url[:200], "method": resp.request.method}
            ),
        )
        page.on(
            "requestfailed",
            lambda req: failed_requests.append(
                {"url": req.url[:200], "failure": str(req.failure)[:200]}
            ),
        )
        page.on(
            "framenavigated",
            lambda frame: (
                nav.append({"url": frame.url[:200]}) if frame == page.main_frame else None
            ),
        )

        business_before = self._business_state()
        testid_presence: dict[str, bool] = {}
        try:
            page.goto(self.base_url + "/projects/new", wait_until="domcontentloaded")
            detail["navigation_ok"] = True
            for tid in EXPECTED_TEST_IDS:
                testid_presence[tid] = page.locator(f'[data-testid="{tid}"]').count() > 0
            detail["expected_testids"] = testid_presence

            # 用户可见语义定位：label + role/name（不使用 test_id）
            name_input = page.get_by_label("项目名称")
            name_input.fill(name)
            submit = page.get_by_role("button", name="创建")
            detail["visible_controls"] = {
                "name_input_by_label": name_input.count() > 0,
                "submit_by_role_name": submit.count() > 0,
            }
            if submit.count() == 0:
                detail["note"] = "用户可见的提交控件不存在：无法通过正常操作完成业务"
                return self._fail_closed(
                    "VISIBLE_CONTROL_MISSING", detail, artifacts, console_errors, responses
                )

            submit.click()
            navigate_started = time.perf_counter()
            navigated, feedback, feedback_ms = self._settle(page)
            settle_ms = (time.perf_counter() - navigate_started) * 1000
            detail["settled_ms"] = round(settle_ms, 1)
            detail["feedback_ms"] = round(feedback_ms, 1)
            detail["feedback_kind"] = feedback
            detail["navigated_to_list"] = navigated
            detail["final_url"] = page.url[:200]

            success_visible = page.get_by_text("项目创建成功").count() > 0
            error_visible = page.get_by_text("创建失败").count() > 0
            message_text = ""
            try:
                message_text = page.locator("#msg, #banner").first.inner_text(timeout=1000)[:200]
            except Exception:  # noqa: BLE001 - 没有提示就是空
                message_text = ""
            detail["ui_feedback"] = {
                "success_visible": success_visible,
                "error_visible": error_visible,
                "text": message_text,
            }

            business_after = self._business_state()
            persisted, persisted_reason = self._persisted(business_after, name)
            detail["business_state"] = {
                "before_count": (business_before.get("payload") or {}).get("count"),
                "before_http_status": business_before.get("http_status"),
                "after_count": (business_after.get("payload") or {}).get("count"),
                "after_http_status": business_after.get("http_status"),
                "persisted": (
                    None if persisted is ReadVerdict.UNKNOWN else persisted is ReadVerdict.PRESENT
                ),
                "persisted_state": persisted.value,
                "persisted_reason": persisted_reason,
                "read_error": business_after.get("error", ""),
                "read_error_detail": business_after.get("error_detail", ""),
                "read_raw": business_after.get("raw", "")[:300],
                "source": "teamflow:/api/state/projects?workspace=default",
            }

            detail["network"] = {
                "responses": responses[-15:],
                "failed_requests": failed_requests[-10:],
            }
            detail["console_errors"] = console_errors[-10:]
            detail["navigations"] = nav[-10:]

            screenshot = self._screenshot(page)
            if screenshot:
                artifacts.append(screenshot)
            dom = self._artifact(".html", page.content())
            if dom:
                artifacts.append(dom)
            aria = self._aria(page)
            if aria:
                artifacts.append(aria)
            evidence.append(
                f"browser:{token}:persisted={persisted.value}:success={success_visible}"
            )

            status, classification, note = self._classify(
                persisted=persisted,
                persisted_reason=persisted_reason,
                success_visible=success_visible,
                error_visible=error_visible,
                navigated=navigated,
                settle_ms=settle_ms,
                testid_presence=testid_presence,
            )
            detail["note"] = note
            detail["observation_elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
            detail["cleanup"] = self._cleanup(token)
            if not detail["cleanup"].get("ok"):
                detail["requires_environment_reset"] = True
                return BrowserObservation(
                    EffectStatus.UNKNOWN.value,
                    "CLEANUP_FAILED",
                    detail,
                    evidence,
                    artifacts,
                )
            return BrowserObservation(status, classification, detail, evidence, artifacts)
        except Exception as exc:  # noqa: BLE001 - 观测本身失败 → UNKNOWN（不推断）
            detail["error"] = f"{type(exc).__name__}: {exc}"[:300]
            detail["console_errors"] = console_errors[-10:]
            detail["network"] = {
                "responses": responses[-10:],
                "failed_requests": failed_requests[-5:],
            }
            detail["cleanup"] = self._cleanup(token)
            if not detail["cleanup"].get("ok"):
                detail["requires_environment_reset"] = True
            return BrowserObservation(
                EffectStatus.UNKNOWN.value, "OBSERVATION_INCOMPLETE", detail, evidence, artifacts
            )
        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响结论
                pass

    def _settle(self, page: Any) -> tuple[bool, str, float]:
        """等待"跳转到列表页 + 出现提示"或"页面内出现提示"。

        返回 `(是否跳到列表页, 提示类型 success|error|none, 从点击到提示的毫秒数)`。
        两者都没有就是静默无反馈（TF-UI-002 / TF-COMP-003 的特征）。
        TF-UI-004 的成功提示会延迟 3000ms 出现，所以等待窗口必须 > 3s。
        """
        started = time.perf_counter()
        deadline = time.time() + self.settle_timeout_ms / 1000
        feedback = "none"
        while time.time() < deadline:
            try:
                if page.get_by_text("项目创建成功").count() > 0:
                    feedback = "success"
                    break
                if page.get_by_text("创建失败").count() > 0:
                    feedback = "error"
                    break
            except Exception:  # noqa: BLE001 - 导航中执行上下文被销毁
                pass
            page.wait_for_timeout(150)
        elapsed_ms = (time.perf_counter() - started) * 1000
        navigated = "/projects" in page.url and "/projects/new" not in page.url
        if feedback == "none":
            # 没有提示时再给一次"跳转是否已经发生"的判定（正常成功页一定有提示，
            # 这里只用于区分"提示缺失"与"根本没提交成功"）
            navigated = "/projects" in page.url and "/projects/new" not in page.url
        return navigated, feedback, elapsed_ms

    def _business_state(self) -> dict[str, Any]:
        return _request_json(
            "GET",
            f"{self.base_url}/api/state/projects?workspace={DEFAULT_WORKSPACE}",
        )

    @staticmethod
    def _persisted(state: dict[str, Any], name: str) -> tuple[ReadVerdict, str]:
        """三态判定业务记录是否存在（RUN-018 WP2）。

        只有 HTTP 200 + ok=true + 合法 projects 列表才允许判定 PRESENT / ABSENT；
        500 / 403 / 连接失败 / 超时 / 非法 JSON / 缺少必要字段一律 UNKNOWN，
        并返回明确的观测失败原因（读取失败 ≠ 业务没有落库）。
        """
        status = state.get("http_status")
        if status != 200:
            reason = str(state.get("error") or f"HTTP_{status or 0}")
            return ReadVerdict.UNKNOWN, f"STATE_READ_HTTP_{status or 0}:{reason}"
        if state.get("error"):
            return ReadVerdict.UNKNOWN, f"STATE_READ_INVALID:{state['error']}"
        payload = state.get("payload")
        if not isinstance(payload, dict):
            return ReadVerdict.UNKNOWN, "STATE_READ_INVALID:NO_PAYLOAD"
        if "ok" not in payload:
            return ReadVerdict.UNKNOWN, "STATE_READ_MALFORMED:MISSING_OK_FIELD"
        if payload.get("ok") is not True:
            return ReadVerdict.UNKNOWN, "STATE_READ_MALFORMED:OK_NOT_TRUE"
        projects = payload.get("projects")
        if not isinstance(projects, list) or any(not isinstance(p, dict) for p in projects):
            return ReadVerdict.UNKNOWN, "STATE_READ_MALFORMED:MISSING_OR_MALFORMED_PROJECTS"
        if any(str(p.get("name")) == name for p in projects):
            return ReadVerdict.PRESENT, "OK"
        # 有效权威读取明确表示记录不在列表里：才允许判 ABSENT
        return ReadVerdict.ABSENT, "OK"

    def _screenshot(self, page: Any) -> str:
        if not self.out_dir:
            return ""
        path = self.out_dir / f"observation-{int(time.time() * 1000)}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
            return str(path)
        except Exception:  # noqa: BLE001
            return ""

    def _aria(self, page: Any) -> str:
        try:
            snapshot = page.locator("body").aria_snapshot()
        except Exception:  # noqa: BLE001 - 老版本 Playwright 没有 aria_snapshot
            return ""
        return self._artifact(".aria.txt", snapshot)

    def _cleanup(self, token: str) -> dict[str, Any]:
        """回收 UI 写入 default workspace 的探针数据（名称带随机 token 前缀）。"""
        read = _request_json(
            "POST",
            f"{self.base_url}/api/test/probe-cleanup",
            body={"token": token, "workspace": DEFAULT_WORKSPACE, "allow_default": True},
            timeout=8.0,
        )
        payload = read.get("payload") or {}
        ok = read.get("http_status") == 200 and bool(payload.get("ok"))
        return {
            "ok": ok,
            "http_status": read.get("http_status"),
            "response": payload or read.get("raw", "")[:300],
            "error": read.get("error", ""),
        }

    def _fail_closed(
        self,
        classification: str,
        detail: dict[str, Any],
        artifacts: list[str],
        console_errors: list[dict[str, Any]],
        responses: list[dict[str, Any]],
    ) -> BrowserObservation:
        detail["console_errors"] = console_errors[-10:]
        detail["network"] = {"responses": responses[-10:]}
        detail["cleanup"] = self._cleanup(str(detail.get("probe_token") or ""))
        if not detail["cleanup"].get("ok"):
            detail["requires_environment_reset"] = True
            return BrowserObservation(
                EffectStatus.UNKNOWN.value, "CLEANUP_FAILED", detail, [], artifacts
            )
        return BrowserObservation(EffectStatus.UNKNOWN.value, classification, detail, [], artifacts)

    @staticmethod
    def _classify(
        *,
        persisted: ReadVerdict,
        persisted_reason: str,
        success_visible: bool,
        error_visible: bool,
        navigated: bool,
        settle_ms: float,
        testid_presence: dict[str, bool],
    ) -> tuple[str, str, str]:
        """把"页面行为 + 业务回读（三态）"映射成效果结论（保守优先）。

        RUN-018 WP2：业务回读 UNKNOWN 时**不得**进入 MISLEADING_SUCCESS_ON_FAILURE /
        SILENT_IGNORE / OPERATION_FAILED_ERROR_SURFACED——独立状态接口 500/403/超时/
        连接失败是观测失败，不是"业务没有落库"的证据。
        """
        if persisted is ReadVerdict.UNKNOWN:
            return (
                EffectStatus.UNKNOWN.value,
                "BUSINESS_STATE_READ_UNAVAILABLE",
                f"独立业务状态回读不可用（{persisted_reason}）：无法确认业务记录是否落库，"
                "不判定业务受影响或未受影响（观测失败 ≠ 业务失效证据）",
            )
        locator_only = not all(testid_presence.values())
        if persisted is ReadVerdict.PRESENT and success_visible and not error_visible:
            if locator_only:
                return (
                    EffectStatus.NOT_TRIGGERED.value,
                    "LOCATOR_ONLY_BREAKAGE",
                    "test_id 发生变化（自动化用例会失败），但用户用可见名称仍能完成业务："
                    "只是定位器资产受影响，不是业务功能失效",
                )
            if settle_ms >= 2000:
                return (
                    EffectStatus.NOT_TRIGGERED.value,
                    "OPERATION_OK_FEEDBACK_DELAYED",
                    f"业务成功，成功提示延迟 {int(settle_ms)}ms 出现（时序观察，不是业务失效）",
                )
            return (
                EffectStatus.NOT_TRIGGERED.value,
                "OPERATION_OK",
                "用户可见操作成功且业务已持久化",
            )
        if persisted is ReadVerdict.PRESENT and error_visible:
            return (
                EffectStatus.CONFIRMED.value,
                "UI_FEEDBACK_CONTRADICTS_BUSINESS",
                "业务已持久化，但页面显示失败提示：用户无法正确判断操作结果",
            )
        if persisted is ReadVerdict.PRESENT and not success_visible and not error_visible:
            return (
                EffectStatus.CONFIRMED.value,
                "UI_FEEDBACK_MISSING",
                "业务已持久化，但页面没有任何成功反馈：用户可见功能受影响",
            )
        if persisted is ReadVerdict.ABSENT and success_visible:
            return (
                EffectStatus.CONFIRMED.value,
                "MISLEADING_SUCCESS_ON_FAILURE",
                "页面显示成功，但独立业务状态确认未落库（False Pass 型用户可见影响）",
            )
        if (
            persisted is ReadVerdict.ABSENT
            and (not success_visible)
            and (not error_visible)
        ):
            return (
                EffectStatus.CONFIRMED.value,
                "SILENT_IGNORE",
                "用户提交后既无反馈也无业务结果：操作被静默忽略",
            )
        if persisted is ReadVerdict.ABSENT and error_visible:
            if locator_only and not navigated:
                return (
                    EffectStatus.NOT_TRIGGERED.value,
                    "LOCATOR_ONLY_BREAKAGE",
                    "定位器资产变化导致自动化用例失败，但用户可见路径仍能走到错误提示",
                )
            return (
                EffectStatus.CONFIRMED.value,
                "OPERATION_FAILED_ERROR_SURFACED",
                "业务操作失败且页面如实显示失败：业务结果受影响",
            )
        return (
            EffectStatus.UNKNOWN.value,
            "INCONCLUSIVE_BROWSER_SIGNALS",
            "页面行为与业务状态不一致或证据不足，无法判定",
        )


__all__ = [
    "BrowserEffectObserver",
    "BrowserObservation",
    "BrowserUnavailable",
    "EXPECTED_TEST_IDS",
    "UI_WORKSPACE",
]
