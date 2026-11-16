"""tests/test_focus_box_rate.py — 확대뷰 박스 갱신률 두 가지 수정 (2026-10-12).

실측(GTX 1650 Ti, 웹캠 fps 30 등록): 확대뷰를 열면 포커스 부스트가 fps 를 5 로 **내려** 박스 갱신 4.2 Hz(간격 254 ms),
포커스 fps 를 10·15·30 으로 올려도 5.8 Hz 상한 — 고속 프레임이 person+ppe 2슬롯(≈125 ms)이기 때문.
고정하는 것:
  ① /cameras/{cid}/focus on: 등록 fps 가 focus_fps 보다 높으면 등록 fps 유지(내리지 않음), 낮으면 focus_fps 로 상향. off: 등록 fps 복원.
  ② worker._focus_detectors(): tuning hub.focus_detectors 를 읽고, 기본은 [person, ppe]; person 이 빠진 잘못된 값은 기본으로.
  ③ 고속 프레임 호출이 _focus_detectors() 를 쓰고, person 전용이면 PPE 는 풀세트 결과(_last_nonperson)로 표시한다(소스 잠금).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "vigent-core")); sys.path.insert(0, str(_ROOT / "tests"))

import tuning  # noqa: E402
import worker  # noqa: E402
from routers import cameras as C  # noqa: E402


class FocusNeverLowersFps(unittest.TestCase):
    def _focus(self, base, on, payload_fps=None, focus_fps=5.0):
        calls = []
        body = {"on": on} | ({"fps": payload_fps} if payload_fps else {})
        with mock.patch.object(C._reg, "get", return_value={"id": "c", "fps": base}), \
             mock.patch("worker.manager.set_fps", side_effect=lambda cid, f: calls.append(f) or {"ok": True, "fps": f}), \
             mock.patch.object(tuning, "val", side_effect=lambda sec, key, d, env=None: focus_fps if (sec, key) == ("hub", "focus_fps") else d):
            C.cameras_focus("c", body)
        return calls[-1]

    def test_high_base_keeps_base(self):
        self.assertEqual(self._focus(30.0, True), 30.0, "등록 30 fps 카메라를 포커스가 5 로 내렸다(실사고)")

    def test_low_base_boosts(self):
        self.assertEqual(self._focus(2.0, True), 5.0)

    def test_payload_fps_also_floored(self):
        self.assertEqual(self._focus(12.0, True, payload_fps=8), 12.0)
        self.assertEqual(self._focus(2.0, True, payload_fps=8), 8.0)

    def test_off_restores_base(self):
        self.assertEqual(self._focus(30.0, False), 30.0)
        self.assertEqual(self._focus(2.0, False), 2.0)


class FocusDetectorsFromTuning(unittest.TestCase):
    def _with(self, value):
        with mock.patch.object(tuning, "val", side_effect=lambda sec, key, d, env=None: value if (sec, key) == ("hub", "focus_detectors") else d):
            return worker._focus_detectors()

    def test_default_is_person_ppe(self):
        self.assertEqual(self._with(worker._FOCUS_DETECTORS_DEFAULT), ["person", "ppe"])

    def test_person_only(self):
        self.assertEqual(self._with(["person"]), ["person"])
        self.assertEqual(self._with("person"), ["person"])

    def test_invalid_falls_back(self):
        for bad in ([], ["ppe"], None, [""]):
            self.assertEqual(self._with(bad), ["person", "ppe"], repr(bad))

    def test_repo_tuning_focus_detectors(self):
        """이 저장소의 config/tuning.yaml focus_detectors — 2026-10-12 사용자가 [person, ppe](보호구도 매 프레임)로 설정.
        바뀌면 의도된 변경인지 확인할 것(보호구↔사람 속도 균형)."""
        self.assertEqual(worker._focus_detectors(), ["person", "ppe"])

    def test_loop_wires_focus_detectors(self):
        from _source_probe import code_of
        code = code_of("vigent-core/worker.py", "_process_frame")
        self.assertIn("_focus_detectors()", code, "고속 프레임이 tuning 검출기 목록을 쓰지 않는다")
        self.assertNotIn('detectors=["person", "ppe"], track_key="cam:" + str(ctx.name) + ":pf"', code)
        self.assertIn("disp_person + self._last_nonperson", code, "person 전용일 때 PPE 가 풀세트 결과로 표시되지 않는다")


class FocusPpeFps(unittest.TestCase):
    """[2026-10-12 보호구 박스 갱신률] hub.focus_ppe_fps — person 전용 고속 경로에 ppe 슬롯을 N Hz 로 끼워 넣는다."""
    def _with(self, value):
        with mock.patch.object(tuning, "val", side_effect=lambda sec, key, d, env=None: value if (sec, key) == ("hub", "focus_ppe_fps") else d):
            return worker._focus_ppe_fps()

    def test_parse_and_clamp(self):
        self.assertEqual(self._with(4), 4.0)
        self.assertEqual(self._with("3"), 3.0)
        self.assertEqual(self._with(0), 0.0)
        self.assertEqual(self._with(-5), 0.0)
        self.assertEqual(self._with(99), 30.0)
        for bad in ("x", None, float("nan")):
            self.assertEqual(self._with(bad), 0.0, repr(bad))

    def test_repo_tuning_value(self):
        self.assertEqual(worker._focus_ppe_fps(), 4.0, "이 PC 설정(2026-10-12 결정) — 바뀌면 의도된 변경인지 확인")

    def test_process_frame_wires_ppe_cadence(self):
        from _source_probe import code_of
        code = code_of("vigent-core/worker.py", "_process_frame")
        self.assertIn("_focus_ppe_fps()", code)
        self.assertIn('_run_ppe = ("ppe" in _fd) or _ppe_now', code, "ppe 차례인 프레임에 ppe 슬롯이 끼지 않는다")
        self.assertIn('pp = guard.detect(frame, detectors=["ppe"], track_key=_pf_ppe_key)', code,
                      "보호구는 별도 호출·별도 키 — 사람 모델과 한 호출에 넣으면 사람 박스가 뒤집힌다(2026-10-12 실사고)")
        self.assertIn("self._last_pf_ppe_ts = t0", code)
        self.assertIn("disp_ppe = self._last_pf_ppe", code, "ppe 차례가 아닌 프레임에서 직전 PPE 박스를 유지하지 않는다(깜빡임)")


if __name__ == "__main__":
    unittest.main()
