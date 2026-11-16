"""`.gitignore` 규칙의 형식 검사 — 기계 상태(git·디스크)에 의존하지 않는 순수 텍스트 검사.

[2026-10-12 실제 사고] `runs/*          # ★'runs/' 가 아니라 …` 처럼 **패턴 줄 끝에 주석**을 붙여 두었는데, gitignore 는 줄 끝 주석을
인식하지 않고 공백·`#`·글자 전부를 패턴의 일부로 본다. 그래서 `runs/*` 규칙이 통째로 무효였고, 학습 체크포인트 31GB(runs/finetune)가
`git status` 에 추적 후보로 떠 있었다(`git check-ignore` 로 확인). 이 테스트는 그 형식 실수의 재발을 막는다.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GITIGNORE = ROOT / ".gitignore"

# 2026-10-12 보강에서 반드시 살아 있어야 하는 규칙(사고 재발 방지용 최소 집합)
MUST_HAVE = ["runs/*", "*.pt", "*.pth", "*.ckpt", "_annotations.coco.json", "audit/gate_*.log", "audit/*_server.log", ".env", "config/notify.yaml"]


def _rule_lines() -> list[tuple[int, str]]:
    out = []
    for i, raw in enumerate(GITIGNORE.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        out.append((i, raw))
    return out


class GitignoreFormat(unittest.TestCase):
    def test_no_trailing_comment_on_rule_lines(self):
        """규칙 줄에 ' # …' 가 붙어 있으면 그 줄 전체가 패턴이 돼 규칙이 무효가 된다 — 한 줄도 허용하지 않는다."""
        bad = [(i, raw) for i, raw in _rule_lines() if re.search(r"\S\s+#", raw)]
        self.assertEqual(bad, [], f"줄 끝 주석이 붙은 규칙 줄(주석은 윗줄에 따로 쓴다): {bad}")

    def test_no_trailing_whitespace_on_rule_lines(self):
        """끝 공백도 패턴의 일부가 된다(백슬래시 이스케이프가 없는 한) — 'foo ' 는 'foo' 를 막지 않는다."""
        bad = [(i, raw) for i, raw in _rule_lines() if raw != raw.rstrip() and not raw.rstrip().endswith("\\")]
        self.assertEqual(bad, [], f"끝 공백이 있는 규칙 줄: {bad}")

    def test_rules_do_not_start_with_indent(self):
        """들여쓴 규칙 줄은 앞 공백까지 패턴이 된다(주석 연속행을 들여쓴 사고의 변형)."""
        bad = [(i, raw) for i, raw in _rule_lines() if raw[:1].isspace()]
        self.assertEqual(bad, [], f"들여쓴 규칙 줄: {bad}")

    def test_required_rules_present(self):
        """사고 뒤 넣은 핵심 규칙이 지워지지 않았는지(정확히 그 줄로 존재)."""
        rules = {raw for _, raw in _rule_lines()}
        missing = [r for r in MUST_HAVE if r not in rules]
        self.assertEqual(missing, [], f".gitignore 에서 빠진 필수 규칙: {missing}")


if __name__ == "__main__":
    unittest.main()
