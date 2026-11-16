"""
base.py — 코어(Guard·Dispatcher)의 공통 베이스.

[정리 5, 2026-10-12] BaseAgent → BaseEngine. 각 코어는 PipelineConfig 를 주입받고, name/role 과 status() 를 공통으로 가진다.
status() 의 필드({"name","role","implemented"})는 /health 의 "agents" 목록에 그대로 실리므로 바꾸지 않는다(응답 불변).
(옛 6-에이전트 설계의 run() 스텁은 호환을 위해 남겨 둔다 — Guard·Dispatcher 는 쓰지 않는다.)
"""
from __future__ import annotations

from typing import Any


class BaseEngine:
    name: str = "base"
    role: str = "(설명 없음)"

    def __init__(self, config: Any):
        # config = vision_loader.PipelineConfig (테마 해석 결과)
        self.config = config

    def status(self) -> dict[str, Any]:
        """등록 상태(/health "agents" 항목 — 필드 불변)."""
        return {"name": self.name, "role": self.role, "implemented": False}

    def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """아직 미구현. 다음 단계에서 채운다."""
        return {
            "agent": self.name,
            "implemented": False,
            "message": f"{self.name} 스텁 — 다음 단계에서 구현 예정",
        }
