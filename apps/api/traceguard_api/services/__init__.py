"""服务容器（路由层依赖注入）。

路由只做参数校验与序列化，业务在 service 层；所有 service 共享同一 Runtime。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from traceguard_api.runtime import Runtime, get_runtime
from traceguard_api.services.diagnosis_service import DiagnosisService
from traceguard_api.services.exploration_service import ExplorationService
from traceguard_api.services.graph_service import GraphService
from traceguard_api.services.probe_service import ProbeService
from traceguard_api.services.repair_service import RepairService
from traceguard_api.services.stability_service import StabilityReplayService
from traceguard_api.services.timeline_service import TimelineService


@dataclass
class Services:
    rt: Runtime
    diagnosis: DiagnosisService
    graph: GraphService
    probe: ProbeService
    repair: RepairService
    exploration: ExplorationService
    timeline: TimelineService
    stability: StabilityReplayService


_services: Services | None = None
_lock = threading.Lock()


def get_services() -> Services:
    global _services
    with _lock:
        if _services is None:
            rt = get_runtime()
            services = Services(
                rt=rt,
                diagnosis=DiagnosisService(rt),
                graph=GraphService(rt),
                probe=ProbeService(rt),
                repair=RepairService(rt),
                exploration=ExplorationService(rt),
                timeline=TimelineService(rt),
                stability=StabilityReplayService(rt),
            )
            # Benchmark 等横切组件通过 Runtime 访问服务（避免循环 import）
            rt.services_diagnosis = services.diagnosis  # type: ignore[attr-defined]
            rt.services_graph = services.graph  # type: ignore[attr-defined]
            rt.services_probe = services.probe  # type: ignore[attr-defined]
            rt.services_repair = services.repair  # type: ignore[attr-defined]
            _services = services
        return _services


def reset_services() -> None:
    global _services
    with _lock:
        _services = None
