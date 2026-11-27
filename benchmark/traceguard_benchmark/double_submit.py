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
