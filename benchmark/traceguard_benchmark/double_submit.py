"""TF-TIME-003 双提交竞态独立观测器（RUN-018 WP6）。

TF-TIME-003 行为：
- 故障激活时，创建接口响应延迟 1.2s
- 前端 JavaScript 在 300ms 内丢弃第二次表单提交（双提交竞态）
- 结果：用户快速双击"创建"按钮时，第二次提交被静默丢弃
- 但第一个请求最终成功（1.2s 后返回）

独立观测目标：
1. 健康基线：单次提交正常创建 1 个项目
2. 故障激活：TF-TIME-003 启用
3. 独立提交：两个浏览器上下文快速双提交
4. 独立状态回读：服务端实际创建了几个项目
5. 数据唯一性检查：是否产生重复数据
6. 清理
7. 原始网络证据

必须区分：
- 用户只产生一次业务操作（第二次被前端去重）→ 服务端只创建 1 条
- 用户触发了两次提交但服务端正确去重 → 服务端只创建 1 条
- 服务端创建了重复数据 → 服务端创建 2 条
- 第一次失败、第二次成功 → 服务端创建 1 条（但来自第二次）
- 探针本身无法触发或无法判断 → UNKNOWN
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fault_effect import EffectStatus, _cleanup_probe_data, _http, _json, new_probe_token


@dataclass
class DoubleSubmitObservation:
    """一次双提交观测的完整结果。"""

    status: str = EffectStatus.UNKNOWN.value
    classification: str = ""
    note: str = ""
    projects_created: int = 0
    expected_created: int = 0
    duplicate_detected: bool = False
    baseline_ok: bool = False
    fault_activated: bool = False
    network_evidence: list[dict[str, Any]] = field(default_factory=list)
    state_readback: dict[str, Any] = field(default_factory=dict)
    cleanup_result: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "classification": self.classification,
            "note": self.note,
            "projects_created": self.projects_created,
            "expected_created": self.expected_created,
            "duplicate_detected": self.duplicate_detected,
            "baseline_ok": self.baseline_ok,
            "fault_activated": self.fault_activated,
            "network_evidence": self.network_evidence,
            "state_readback": self.state_readback,
            "cleanup_result": self.cleanup_result,
        }


class DoubleSubmitObserver:
    """TF-TIME-003 双提交竞态独立观测器。

    使用 HTTP 直接模拟快速双提交（不依赖浏览器——浏览器层的 300ms
    去重是前端行为，我们用两个几乎同时的 HTTP 请求来测试服务端行为）。
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        out_dir: str | Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.out_dir = Path(out_dir) if out_dir else None

    def observe(self, fault_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """执行一次双提交观测。"""
        faults = [str(f) for f in fault_ids]
        is_double_submit = "TF-TIME-003" in faults

        if not is_double_submit:
            return {
                "fault_effect_status": EffectStatus.UNKNOWN.value,
                "fault_effect_source": "DOUBLE_SUBMIT_OBSERVER:NOT_APPLICABLE",
                "fault_effect_note": "非双提交场景，本观测器不适用",
                "fault_effect_classification": "NOT_APPLICABLE",
            }

        token = new_probe_token()
        started = time.perf_counter()
        obs = DoubleSubmitObservation()

        try:
            # Step 1: 健康基线（故障未激活时单次提交应创建 1 个项目）
            obs.baseline_ok = self._health_baseline(token, obs)

            # Step 2: 故障激活状态确认
            obs.fault_activated = self._check_fault_active("TF-TIME-003")

            # Step 3: 独立双提交
            self._double_submit(token, obs)

            # Step 4: 独立状态回读
            self._state_readback(token, obs)

            # Step 5: 数据唯一性判定
            self._classify(obs)

        except Exception as exc:  # noqa: BLE001
            obs.status = EffectStatus.UNKNOWN.value
            obs.classification = "OBSERVER_ERROR"
            obs.note = f"观测器异常：{type(exc).__name__}: {exc}"[:300]

        finally:
            # Step 6: 清理
            obs.cleanup_result = _cleanup_probe_data(self.base_url, token, self.timeout)

        elapsed = round((time.perf_counter() - started) * 1000, 1)
        payload = obs.to_dict()
        return {
            "fault_effect_status": obs.status,
            "fault_effect_source": "DOUBLE_SUBMIT_OBSERVER:http_rapid_submit",
            "fault_effect_evidence_refs": [],
            "fault_effect_probes": [
                {
                    "kind": "double_submit",
                    "impacted": obs.status == EffectStatus.CONFIRMED.value,
                    "classification": obs.classification,
                    "detail": payload,
                    "elapsed_ms": elapsed,
                }
            ],
            "fault_effect_classification": obs.classification,
            "fault_effect_note": obs.note,
            "fault_effect_requires_environment_reset": False,
            "double_submit_observation": payload,
        }

    # ------------------------------------------------------------------ steps
    def _health_baseline(self, token: str, obs: DoubleSubmitObservation) -> bool:
        """故障前基线：单次提交创建 1 个项目。"""
        name = f"bp:{token}:baseline"
        status, text, ms = _http(
            "POST",
            self.base_url + "/api/projects",
            body={"name": name, "workspace_slug": "tg-benchmark-probe"},
            timeout=self.timeout,
        )
        obs.network_evidence.append(
            {
                "step": "baseline_create",
                "status": status,
                "elapsed_ms": ms,
                "response": text[:500],
            }
        )
        if status == 200 or status == 201:
            data = _json(text)
            if data.get("key") or data.get("id"):
                return True
        return False

    def _check_fault_active(self, fault_id: str) -> bool:
        """检查故障是否已激活。"""
        status, text, _ = _http(
            "GET",
            self.base_url + "/api/faults",
            timeout=self.timeout,
        )
        if status == 200:
            data = _json(text)
            active = data.get("active") or data.get("active_faults") or []
            return fault_id in active
        return False

    def _double_submit(self, token: str, obs: DoubleSubmitObservation) -> None:
        """快速双提交：两个几乎同时的 HTTP POST（urllib，不引入 requests 依赖）。"""
        name1 = f"bp:{token}:submit1"
        name2 = f"bp:{token}:submit2"
        url = self.base_url + "/api/projects"

        results: list[tuple[int, str, float]] = []
        lock = threading.Lock()

        def _submit(name: str):
            start = time.perf_counter()
            try:
                data = json.dumps({"name": name, "workspace_slug": "tg-benchmark-probe"}).encode()
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    text = resp.read(8192).decode("utf-8", "replace")
                    elapsed = (time.perf_counter() - start) * 1000
                    with lock:
                        results.append((resp.status, text[:500], elapsed))
            except urllib.error.HTTPError as exc:
                text = exc.read(8192).decode("utf-8", "replace")
                elapsed = (time.perf_counter() - start) * 1000
                with lock:
                    results.append((exc.code, text[:500], elapsed))
            except Exception as exc:  # noqa: BLE001
                elapsed = (time.perf_counter() - start) * 1000
                with lock:
                    results.append((0, f"ERROR: {exc}"[:500], elapsed))

        t1 = threading.Thread(target=_submit, args=(name1,))
        t2 = threading.Thread(target=_submit, args=(name2,))
        t1.start()
        time.sleep(0.02)  # 20ms 间隔，模拟快速双击
        t2.start()
        t1.join(timeout=self.timeout + 2)
        t2.join(timeout=self.timeout + 2)

        for i, (status, text, ms) in enumerate(results):
            obs.network_evidence.append(
                {
                    "step": f"double_submit_{i + 1}",
                    "status": status,
                    "elapsed_ms": round(ms, 1),
                    "response": text,
                }
            )

    def _state_readback(self, token: str, obs: DoubleSubmitObservation) -> None:
        """独立状态回读：查看服务端实际创建了多少项目。"""
        status, text, ms = _http(
            "GET",
            self.base_url + "/api/projects?workspace_slug=tg-benchmark-probe",
            timeout=self.timeout,
        )
        data = _json(text) if status == 200 else {}
        projects = data.get("projects") or data.get("items") or []
        if isinstance(projects, list):
            bp_projects = [p for p in projects if str(p.get("name", "")).startswith(f"bp:{token}:")]
            obs.projects_created = len(bp_projects)
            obs.state_readback = {
                "http_status": status,
                "total_projects": len(projects),
                "bp_projects": len(bp_projects),
                "bp_names": [p.get("name") for p in bp_projects],
                "elapsed_ms": ms,
            }
        else:
            obs.state_readback = {
                "http_status": status,
                "error": "invalid response structure",
                "raw": text[:500],
            }

    def _classify(self, obs: DoubleSubmitObservation) -> None:
        """根据观测结果分类。"""
        created = obs.projects_created

        if not obs.baseline_ok:
            obs.status = EffectStatus.UNKNOWN.value
            obs.classification = "BASELINE_FAILED"
            obs.note = "健康基线失败，无法判定双提交行为"
            return

        if created == 0:
            obs.status = EffectStatus.UNKNOWN.value
            obs.classification = "NO_DATA"
            obs.note = "服务端未创建任何项目（可能是双提交都被丢弃，或服务端故障）"
            return

        if created == 1:
            # 只创建了 1 个项目
            # 需要区分：是前端去重（用户只产生一次操作）还是服务端去重
            # 从观测角度看：两个 HTTP 请求都发出了（网络证据可查）
            # 如果两个请求都返回 200/201，但只创建 1 条 → 服务端去重或前端拦截
            # 如果一个成功一个失败 → 第一次失败第二次成功
            statuses = [
                e["status"] for e in obs.network_evidence if e["step"].startswith("double_submit_")
            ]
            ok_count = sum(1 for s in statuses if s in (200, 201))

            if ok_count == 2:
                obs.status = EffectStatus.CONFIRMED.value
                obs.classification = "DOUBLE_SUBMIT_DEDUPED"
                obs.note = (
                    "双提交竞态确认：两个请求都返回成功，但服务端只创建 1 个项目。"
                    "TF-TIME-003 的 300ms 去重窗口生效（或服务端正确去重）。"
                )
            elif ok_count == 1:
                obs.status = EffectStatus.CONFIRMED.value
                obs.classification = "DOUBLE_SUBMIT_ONE_FAILED"
                obs.note = "一个提交成功一个失败，只创建 1 个项目"
            else:
                obs.status = EffectStatus.UNKNOWN.value
                obs.classification = "SUBMIT_FAILED"
                obs.note = "双提交都失败，无法判定"
            return

        if created == 2:
            obs.status = EffectStatus.NOT_TRIGGERED.value
            obs.classification = "DUPLICATE_CREATED"
            obs.duplicate_detected = True
            obs.note = f"服务端创建了 {created} 个项目——双提交竞态未去重，产生了重复数据"
            return

        if created > 2:
            obs.status = EffectStatus.UNKNOWN.value
            obs.classification = "UNEXPECTED_COUNT"
            obs.note = f"预期最多 2 个项目，实际创建 {created} 个（含基线）"
            return

        obs.status = EffectStatus.UNKNOWN.value
        obs.classification = "UNCLASSIFIED"
        obs.note = f"无法分类：created={created}"


# ============================================================================
# RUN-018 WP4（R1 轮）：真实浏览器独立观测（Playwright Chromium）
#
# 上一轮的 HTTP 双线程观测只能验证服务端行为，不能回答"用户双击后前端到底
# 发了几个请求"。本观测器补上两个真实浏览器实验：
#
# 实验 A（同一 BrowserContext 快速双击）：
#   - 记录用户点击次数（脚本控制：2 次 click）
#   - 通过 page.on("request") 捕获真实发出的 POST /api/projects 请求数量
#   - 记录每个请求的状态码与耗时
#   - 权威状态回读（/api/state/projects）得出业务实际创建数量
#   区分：用户产生两次点击 ≠ 前端发送一次请求 ≠ 前端发送两次请求
#
# 实验 B（两个独立 BrowserContext 并发提交）：
#   - 独立登录态 / 独立会话（各自独立 Playwright 实例，跨线程）
#   - 近似同时发起业务提交，记录请求与响应
#   - 权威状态回读检查重复数据 / 服务端幂等行为
#
# 分类（实验 A 与 B 严格区分，不合并）：
#   FRONTEND_DEBOUNCE_EFFECTIVE   双击 2 次 → 前端只发 1 次请求 → 1 条
#   SERVER_IDEMPOTENCY_EFFECTIVE  ≥2 次请求成功 → 服务端只落 1 条
#   DUPLICATE_CREATED             服务端落了 ≥2 条（重复数据）
#   ONE_REQUEST_FAILED            一败一成 → 1 条（语义归属不明确）
#   BUSINESS_STATE_UNKNOWN        无法回读权威状态
#   OBSERVER_FAILED               观测器基础设施失败（浏览器启动失败等）
# ============================================================================

#: 浏览器实验创建的项目固定在 default workspace（TeamFlow UI 表单行为），
#: 清理必须显式 allow_default（token ≥12 位 hex，与 WP3 清理契约一致）。
_BROWSER_WORKSPACE = "default"

#: 实验分类常量
FRONTEND_DEBOUNCE_EFFECTIVE = "FRONTEND_DEBOUNCE_EFFECTIVE"
SERVER_IDEMPOTENCY_EFFECTIVE = "SERVER_IDEMPOTENCY_EFFECTIVE"
DUPLICATE_CREATED = "DUPLICATE_CREATED"
ONE_REQUEST_FAILED = "ONE_REQUEST_FAILED"
BUSINESS_STATE_UNKNOWN = "BUSINESS_STATE_UNKNOWN"
OBSERVER_FAILED = "OBSERVER_FAILED"


def _browser_state_readback(base_url: str, token: str, timeout: float) -> dict[str, Any]:
    """权威状态回读：default workspace 中 bp:<token>: 前缀的项目。"""
    status, text, ms = _http(
        "GET",
        base_url + f"/api/state/projects?workspace={_BROWSER_WORKSPACE}",
        timeout=timeout,
    )
    data = _json(text) if status == 200 else {}
    projects = data.get("projects") or []
    names = [str(p.get("name", "")) for p in projects if isinstance(p, dict)]
    mine = [n for n in names if n.startswith(f"bp:{token}:")]
    return {
        "http_status": status,
        "count": len(mine),
        "names": mine,
        "workspace_total": len(names),
        "elapsed_ms": ms,
    }


def _browser_cleanup(base_url: str, token: str, timeout: float) -> dict[str, Any]:
    """清理 default workspace 中本探针的数据（显式 allow_default，失败如实报告）。"""
    status, text, ms = _http(
        "POST",
        base_url + "/api/test/probe-cleanup",
        body={
            "token": token,
            "workspace": _BROWSER_WORKSPACE,
            "allow_default": True,
        },
        timeout=max(timeout, 8.0),
    )
    payload = _json(text)
    ok = (
        status == 200
        and bool(payload.get("ok"))
        and int(payload.get("remaining_matched") or 0) == 0
    )
    return {
        "ok": ok,
        "http_status": status,
        "elapsed_ms": round(ms, 1),
        "response": payload or text[:300],
    }


class _RequestCapture:
    """捕获页面发出的业务 POST 请求（URL / 状态码 / 耗时）。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._pending: dict[int, float] = {}

    def on_request(self, request: Any) -> None:
        if "/api/projects" in request.url and request.method == "POST":
            self._pending[id(request)] = time.perf_counter()
            self.requests.append({"url": request.url[:200], "status": None, "elapsed_ms": None})

    def on_response(self, response: Any) -> None:
        if "/api/projects" in response.url and response.request.method == "POST":
            start = self._pending.pop(id(response.request), None)
            elapsed = (time.perf_counter() - start) * 1000 if start else None
            for r in reversed(self.requests):
                if r["url"] == response.url[:200] and r["status"] is None:
                    r["status"] = response.status
                    r["elapsed_ms"] = round(elapsed, 1) if elapsed else None
                    break

    def posts(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if "/api/projects" in r["url"]]


class BrowserDoubleSubmitObserver:
    """真实浏览器双提交独立观测（实验 A：同 context 双击；实验 B：双 context 并发）。"""

    def __init__(
        self,
        base_url: str,
        *,
        out_dir: str | Path | None = None,
        headless: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.out_dir = Path(out_dir) if out_dir else None
        self.headless = headless
        self.timeout = timeout

    # ------------------------------------------------------------------ main
    def observe(self, *, fault_active: bool) -> dict[str, Any]:
        """执行实验 A + B 并分类。fault_active 表示 TF-TIME-003 是否处于激活态
        （由调用方通过 /api/faults 确认；分类结论需结合 Ground Truth 解读）。"""
        started = time.perf_counter()
        token = new_probe_token()
        result: dict[str, Any] = {
            "observer": "BrowserDoubleSubmitObserver",
            "probe_token": token,
            "workspace": _BROWSER_WORKSPACE,
            "fault_active": fault_active,
            "experiment_a": {},
            "experiment_b": {},
            "state_readback": {},
            "cleanup": {},
            "classification": "",
            "status": EffectStatus.UNKNOWN.value,
            "note": "",
            "ground_truth_interpretation": "",
        }
        try:
            pass
        except Exception as exc:  # noqa: BLE001
            result["classification"] = OBSERVER_FAILED
            result["note"] = f"Playwright 不可用: {type(exc).__name__}: {exc}"[:300]
            return self._finalize(result, started, token)

        try:
            # ---- 实验 A：同一 BrowserContext 快速双击
            result["experiment_a"] = self._experiment_a(token)
            # ---- 实验 B：两个独立 BrowserContext 并发提交
            result["experiment_b"] = self._experiment_b(token)
            # ---- 权威状态回读（按项目名区分实验 A / B 的归属计数）
            readback = _browser_state_readback(self.base_url, token, self.timeout)
            names = readback.get("names") or []
            readback["expA_count"] = sum(1 for n in names if ":expA" in n)
            readback["expB1_count"] = sum(1 for n in names if ":expB1" in n)
            readback["expB2_count"] = sum(1 for n in names if ":expB2" in n)
            readback["expB_count"] = readback["expB1_count"] + readback["expB2_count"]
            result["state_readback"] = readback
            # ---- 分类（A 与 B 分别归类，不合并）
            self._classify(result)
        except Exception as exc:  # noqa: BLE001
            result["classification"] = OBSERVER_FAILED
            result["status"] = EffectStatus.UNKNOWN.value
            result["note"] = f"观测器异常：{type(exc).__name__}: {exc}"[:300]
        finally:
            result["cleanup"] = _browser_cleanup(self.base_url, token, self.timeout)
        return self._finalize(result, started, token)

    def _finalize(self, result: dict[str, Any], started: float, token: str) -> dict[str, Any]:
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        classification = result["classification"]
        # 状态映射：只有"观测确认了真实故障行为"才是 CONFIRMED；
        # 幂等/去重生效不是故障；UNKNOWN 不允许被改写成正常。
        if classification == FRONTEND_DEBOUNCE_EFFECTIVE:
            result["status"] = (
                EffectStatus.CONFIRMED.value
                if result.get("fault_active")
                else EffectStatus.NOT_TRIGGERED.value
            )
            result["ground_truth_interpretation"] = (
                "TF-TIME-003 设计行为：前端 300ms 去重窗口丢弃第二次提交（用户第二次点击丢失）"
                if result.get("fault_active")
                else "故障未激活时前端仍去重（非故障行为）"
            )
        elif classification == DUPLICATE_CREATED:
            result["status"] = EffectStatus.CONFIRMED.value
            result["ground_truth_interpretation"] = "服务端产生重复数据——独立观测确认真实故障"
        elif classification == SERVER_IDEMPOTENCY_EFFECTIVE:
            result["status"] = EffectStatus.NOT_TRIGGERED.value
            result["ground_truth_interpretation"] = (
                "服务端幂等去重生效，未产生重复数据（非双提交故障）"
            )
        elif classification in (ONE_REQUEST_FAILED, BUSINESS_STATE_UNKNOWN):
            result["status"] = EffectStatus.UNKNOWN.value
        else:
            result["status"] = EffectStatus.UNKNOWN.value
        if self.out_dir:
            try:
                self.out_dir.mkdir(parents=True, exist_ok=True)
                (self.out_dir / f"double-submit-browser-{token[:12]}.json").write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception:  # noqa: BLE001 - 证据落盘失败不影响结论
                pass
        result["elapsed_ms"] = elapsed
        return result

    # ------------------------------------------------------------- experiment A
    def _experiment_a(self, token: str) -> dict[str, Any]:
        """同一 BrowserContext：填写一次 → 快速双击创建 → 捕获真实请求数。"""
        from playwright.sync_api import sync_playwright

        detail: dict[str, Any] = {"context": "single", "clicks_attempted": 2}
        capture = _RequestCapture()
        console_errors: list[dict[str, Any]] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                detail["browser_context_id"] = id(context) % 100000
                page = context.new_page()
                page.set_default_timeout(self.timeout * 1000)
                page.on("request", capture.on_request)
                page.on("response", capture.on_response)
                page.on(
                    "console",
                    lambda msg: (
                        console_errors.append({"type": msg.type, "text": msg.text[:200]})
                        if msg.type == "error"
                        else None
                    ),
                )
                page.goto(self.base_url + "/projects/new", wait_until="domcontentloaded")
                name_input = page.get_by_label("项目名称")
                name_input.fill(f"bp:{token}:expA")
                submit = page.get_by_role("button", name="创建")
                if submit.count() == 0:
                    detail["classification"] = OBSERVER_FAILED
                    detail["note"] = "可见创建按钮不存在"
                    return detail
                # 快速双击：两次 click 之间无等待（真实用户双击节奏）
                submit.click()
                delivered = 1
                second_note = ""
                try:
                    # 第二次点击必须立即发出（300ms 去重窗口内）。
                    # 无故障时首次点击可能立即导航跳转导致按钮消失——如实记录，
                    # 不视为观测失败（此时实验 A 无法回答双击语义，交由实验 B）。
                    submit.click(timeout=500)
                    delivered = 2
                except Exception as exc:  # noqa: BLE001
                    second_note = f"second_click_not_delivered: {type(exc).__name__}"[:200]
                detail["clicks_delivered"] = delivered
                detail["second_click_note"] = second_note
                # 等待网络安静（最长 timeout）
                try:
                    page.wait_for_load_state("networkidle", timeout=self.timeout * 1000)
                except Exception:  # noqa: BLE001 - TF-TIME-003 延迟 1.2s 可能超时
                    pass
                page.wait_for_timeout(300)
                if self.out_dir:
                    try:
                        self.out_dir.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(self.out_dir / f"exp-a-{token[:12]}.png"))
                    except Exception:  # noqa: BLE001
                        pass
                context.close()
            finally:
                browser.close()
        detail["console_errors"] = console_errors[:10]
        detail["http_requests"] = capture.posts()
        detail["request_count"] = len(capture.posts())
        detail["ok_responses"] = sum(1 for r in capture.posts() if r["status"] in (200, 201))
        return detail

    # ------------------------------------------------------------- experiment B
    def _experiment_b(self, token: str) -> dict[str, Any]:
        """两个独立 BrowserContext（独立 Playwright 实例 + 独立会话）并发提交。"""
        import threading

        results: list[dict[str, Any]] = []
        barrier = threading.Barrier(2, timeout=20)

        def _worker(idx: int) -> None:
            from playwright.sync_api import sync_playwright

            capture = _RequestCapture()
            detail: dict[str, Any] = {"context": f"independent-{idx}"}
            try:
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(headless=self.headless)
                    try:
                        context = browser.new_context(viewport={"width": 1280, "height": 800})
                        page = context.new_page()
                        page.set_default_timeout(self.timeout * 1000)
                        page.on("request", capture.on_request)
                        page.on("response", capture.on_response)
                        page.goto(self.base_url + "/projects/new", wait_until="domcontentloaded")
                        page.get_by_label("项目名称").fill(f"bp:{token}:expB{idx}")
                        submit = page.get_by_role("button", name="创建")
                        barrier.wait()  # 两个上下文对齐后同时点击
                        submit.click()
                        try:
                            page.wait_for_load_state("networkidle", timeout=self.timeout * 1000)
                        except Exception:  # noqa: BLE001
                            pass
                        if self.out_dir:
                            try:
                                self.out_dir.mkdir(parents=True, exist_ok=True)
                                page.screenshot(
                                    path=str(self.out_dir / f"exp-b{idx}-{token[:12]}.png")
                                )
                            except Exception:  # noqa: BLE001
                                pass
                        context.close()
                    finally:
                        browser.close()
                detail["http_requests"] = capture.posts()
                detail["request_count"] = len(capture.posts())
                detail["ok_responses"] = sum(
                    1 for r in capture.posts() if r["status"] in (200, 201)
                )
                detail["error"] = ""
            except Exception as exc:  # noqa: BLE001
                detail["error"] = f"{type(exc).__name__}: {exc}"[:300]
                try:
                    barrier.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            results.append(detail)

        t1 = threading.Thread(target=_worker, args=(1,))
        t2 = threading.Thread(target=_worker, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=self.timeout * 3)
        t2.join(timeout=self.timeout * 3)
        return {"contexts": results}

    # ------------------------------------------------------------------ classify
    def _classify(self, result: dict[str, Any]) -> None:
        """实验 A / B 分别归类（严格区分，不合并）。"""
        a = result.get("experiment_a") or {}
        b = result.get("experiment_b") or {}
        readback = result.get("state_readback") or {}
        total_created = int(readback.get("count") or -1)

        if readback.get("http_status") != 200:
            result["classification"] = BUSINESS_STATE_UNKNOWN
            result["note"] = "权威状态回读失败，业务实际结果未知"
            return

        a_reqs = int(a.get("request_count") or 0)
        a_ok = int(a.get("ok_responses") or 0)
        a_clicks = int(a.get("clicks_delivered") or 0)

        b_ctxs = b.get("contexts") or []
        b_errors = [c for c in b_ctxs if c.get("error")]
        b_reqs = sum(int(c.get("request_count") or 0) for c in b_ctxs)
        b_ok = sum(int(c.get("ok_responses") or 0) for c in b_ctxs)

        exp_a_created = int(readback.get("expA_count") or 0)
        exp_b_created = int(readback.get("expB_count") or 0)

        # ---- 实验 A 优先（同一会话内的双击语义）
        if a_clicks == 2 and a_reqs > 0:
            if a_reqs == 1 and a_ok == 1 and exp_a_created == 1:
                result["classification"] = FRONTEND_DEBOUNCE_EFFECTIVE
                result["note"] = (
                    "实验 A：用户双击 2 次，前端只发出 1 次 POST，业务只创建 1 条"
                    "——前端去重（debounce）生效"
                )
                return
            if a_reqs >= 2 and exp_a_created == 1:
                if a_ok == a_reqs:
                    result["classification"] = SERVER_IDEMPOTENCY_EFFECTIVE
                    result["note"] = (
                        "实验 A：前端发出 ≥2 次请求且全部成功，业务只创建 1 条——服务端幂等/去重生效"
                    )
                else:
                    result["classification"] = ONE_REQUEST_FAILED
                    result["note"] = "实验 A：多次请求部分失败，只创建 1 条（语义归属需结合响应体）"
                return
            if a_reqs >= 2 and exp_a_created >= 2:
                result["classification"] = DUPLICATE_CREATED
                result["note"] = (
                    f"实验 A：前端发出 {a_reqs} 次请求，业务创建 {exp_a_created} 条——重复数据"
                )
                result["duplicate_detected"] = True
                return
            if a_reqs == 1 and a_ok == 0:
                result["classification"] = ONE_REQUEST_FAILED
                result["note"] = "实验 A：唯一请求失败，无业务结果"
                return

        # ---- 实验 B（独立会话并发语义）
        if not b_errors and b_reqs > 0 and total_created >= 0:
            if b_ok == b_reqs and b_reqs >= 2 and exp_b_created >= 2:
                result["classification"] = DUPLICATE_CREATED
                result["note"] = (
                    f"实验 B：两个独立上下文各发 1 次请求全部成功，"
                    f"业务创建 {exp_b_created} 条——跨会话并发产生重复数据"
                )
                result["duplicate_detected"] = True
                return
            if b_ok == b_reqs and b_reqs >= 2 and exp_b_created == 1:
                result["classification"] = SERVER_IDEMPOTENCY_EFFECTIVE
                result["note"] = (
                    "实验 B：两个独立上下文的提交全部成功，业务只创建 1 条——服务端幂等生效"
                )
                return
            if b_ok < b_reqs and total_created <= 1:
                result["classification"] = ONE_REQUEST_FAILED
                result["note"] = "实验 B：并发提交中部分失败，业务结果不明确"
                return

        # ---- 无法判定
        if b_errors:
            result["classification"] = OBSERVER_FAILED
            result["note"] = f"实验 B 上下文异常: {b_errors[0].get('error', '')[:200]}"
            return
        result["classification"] = BUSINESS_STATE_UNKNOWN
        result["note"] = (
            f"无法分类：A(clicks={a_clicks}, reqs={a_reqs}, ok={a_ok}) "
            f"B(reqs={b_reqs}, ok={b_ok}) createdA={exp_a_created} createdB={exp_b_created}"
        )
