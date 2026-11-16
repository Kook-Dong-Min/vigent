"""
engine 패키지 — 감시 서버 코어 2종: Guard(검출·추적) · Dispatcher(통보).

[정리 5, 2026-10-12] 폴더 이름을 agents → engine 으로 바꿨다(git mv). 비전 전용 저장소에 "에이전트" 라는 이름이 남아
LLM 층과 혼동되는 것을 막기 위한 것. 클래스도 GuardAgent→Guard · DispatcherAgent→Dispatcher · BaseAgent→BaseEngine.
**바꾸지 않은 것(응답·로그 호환):** load_theme 번들의 "agents" 키 · /health 의 "agents" 키 · 각 코어의 name 값("Guard"/"Dispatcher") ·
status() 필드 · 로거 이름(vigent.guard / vigent.dispatcher) · build_agents / AGENT_CLASSES 이름.
[⑤-2, 2026-10-12] LLM 에이전트 4종(Analyst·Scribe·Copilot·SafetyManager)은 별도 에이전트 저장소로 옮기기 위해
**삭제됐다**(보관: 태그 archive/with-agents-20261012, 계획: md/MERGE_PLAN_20261012.md).
"""
from __future__ import annotations

from typing import Any

from .base import BaseEngine
from .dispatcher import Dispatcher
from .guard import Guard

AGENT_CLASSES: list[type[BaseEngine]] = [Guard, Dispatcher]   # 감지 → 통보


def build_agents(config: Any) -> dict[str, BaseEngine]:
    """코어(Guard·Dispatcher)를 인스턴스화해 {이름: 인스턴스} 로 반환(키는 cls.name — 번들 "agents" 키와 함께 불변)."""
    return {cls.name: cls(config) for cls in AGENT_CLASSES}


__all__ = ["BaseEngine", "Guard", "Dispatcher", "AGENT_CLASSES", "build_agents"]
