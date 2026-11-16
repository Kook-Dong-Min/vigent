"""tests/test_box_render_smoothing.py — 확대뷰 박스 렌더 평활(renderTau) (2026-10-12).

사용자 보고 "박스가 사람을 매끄럽게 따라가면 좋겠다". 라이브 기록(9 Hz 갱신, 수신 나이 134 ms) 60 fps 시뮬:
외삽 상한(0.85×간격≈96 ms) < lead(134 ms) 라 외삽이 즉시 포화 → 렌더 프레임의 82% 가 정지, 갱신 순간만 툭툭(계단).
BoxTracker 에 renderTau(기본 0=기존) 를 두고 허브가 80 ms 를 켠다 → 정지 82→28%, 갱신 점프 p95 4.1→0.8 px.
고정하는 것:
  ① 모듈: renderTau=0 이면 sample() 결과가 목표 박스와 동일(기존 동작 0 변화)
  ② 모듈: renderTau>0 이면 갱신 직후 표시 박스가 목표로 **점진** 이동(한 번에 안 뛰고, 수 프레임 뒤 수렴)
  ③ 허브가 renderTau 를 켠다(소스 잠금)
(node 가 없으면 ①②는 skip — 허브 소스 잠금은 항상 검사)
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MOD = _ROOT / "vigent-core" / "static" / "vigent-box-display.js"
_JS = r"""
const { BoxTracker } = require(process.argv[2]).VigentBoxDisplay;
function run(opts){ const bt=new BoxTracker(opts); const A=[100,100,50,80], B=[160,100,50,80];
  // 60fps 렌더를 계속 돌리면서 1000·1110 에 정지 박스(속도 0), 1220 에 60px 점프 박스를 ingest
  const xs=[]; const ev={1000:A,1110:A,1220:B};
  for(let t=1000;t<=1220+400;t+=16.7){ const k=Object.keys(ev).map(Number).find(e=>e>t-16.7&&e<=t); if(k!==undefined) bt.ingest([{cls:'person',score:.9,id:1,box:ev[k]}], k, k);
    const v=bt.sample(t).filter(o=>o.cls==='person'); if(t>=1220) xs.push(v.length?+v[0].box[0].toFixed(2):null); }
  return xs; }
console.log(JSON.stringify({off:run({extrapCap:0.85}), on:run({extrapCap:0.85,renderTau:80})}));
"""


class RenderSmoothing(unittest.TestCase):
    def test_hub_enables_render_tau(self):
        html = (_ROOT / "themes" / "safety" / "index_hub.html").read_text(encoding="utf-8")
        # [2026-10-12 설정 통합] 박스 옵션은 tuning.yaml hub.box_* 에서 주입된다. 허브 HTML 은 주입값을 읽고(없으면 폴백) renderTau 를 쓴다.
        self.assertIn("window.VIGENT_HUB_CFG", html, "허브가 주입 설정을 읽지 않는다")
        self.assertRegex(html, r"renderTau:\(_hc\.box_render_tau!=null\?_hc\.box_render_tau:\d+\)", "renderTau 가 tuning 주입+폴백 형태가 아니다")
        self.assertIn("spotAutoLead+(_spotBt.renderTau||0)-dispOff(spotId)", html,
                      "샘플 시점에 τ 를 더하지 않으면 평활 지연(≈τ)이 그대로 남는다 — 2026-10-12 '추적이 느리다' 보고")

    def test_safety_hub_injects_tuning_box_cfg(self):
        import sys as _sys
        _sys.path.insert(0, str(_ROOT / "vigent-core"))
        import tuning
        from routers.safety_core import safety_hub
        page = safety_hub()
        self.assertIn("window.VIGENT_HUB_CFG=", page, "safety_hub 가 박스 설정을 주입하지 않는다")
        self.assertIn('"box_render_tau"', page)
        self.assertIn(str(int(float(tuning.val("hub", "box_render_tau", 40.0)))), page.split("window.VIGENT_HUB_CFG=")[1][:120])

    def test_module_behaviour(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node 없음")
        import tempfile
        with tempfile.TemporaryDirectory() as d:          # `node -e` 는 추가 인자 위치가 달라 argv[2] 가 비므로 파일로 실행
            js = Path(d) / "sim.js"; js.write_text(_JS, encoding="utf-8")
            out = subprocess.run([node, str(js), str(_MOD)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout)
        off, on = r["off"], r["on"]
        # ① 기존: 갱신 직후 첫 렌더에서 이미 목표 근처(한 번에 뜀). One-Euro 가 60px 점프를 일부 평활하므로 '정확히 160' 은 요구하지 않고
        #    첫 프레임 이동이 on 보다 훨씬 커야 한다.
        self.assertGreater(off[0] - 100, 3 * (on[0] - 100), f"renderTau 가 꺼졌는데 점진 이동처럼 보인다 off={off[:4]} on={on[:4]}")
        # ② on: 단조 증가로 수렴, 400 ms 뒤엔 off 와 거의 같은 자리
        self.assertTrue(all(b >= a - 1e-6 for a, b in zip(on, on[1:])), f"on 이 단조 접근하지 않는다 {on[:8]}")
        self.assertLess(abs(on[-1] - off[-1]), 2.0, f"수렴 위치가 다르다 on={on[-1]} off={off[-1]}")
        self.assertLess(on[0] - 100, 20, "첫 프레임부터 거의 다 뛰었다(평활 미작동)")


if __name__ == "__main__":
    unittest.main()
