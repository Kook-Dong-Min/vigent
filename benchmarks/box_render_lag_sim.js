// 합성 이동(좌우 왕복, 진폭 150px, 주기 3s) 박스를 9 Hz 갱신·수신 나이 134 ms 로 BoxTracker 에 넣고,
// 60fps 렌더에서 '표시 박스 중심' 이 '그 순간의 실제 위치' 보다 얼마나 늦는지(시간 지연 ms, 위치 오차 px) 잰다.
'use strict';
const path = require('path');
const { BoxTracker } = require(require('path').join(__dirname, '..', 'vigent-core', 'static', 'vigent-box-display.js')).VigentBoxDisplay;
const opts = JSON.parse(process.argv[2] || '{}'); const leadMode = process.argv[3] || 'median';   // median | capped
const HZ = 9, AGE = 134, FPS = 60, AMP = 150, PER = 3000, W = 50, H = 80;
const truth = t => 320 + AMP * Math.sin(2 * Math.PI * t / PER);
const bt = new BoxTracker(opts);
const gap = 1000 / HZ; let lead = AGE + (opts.leadExtra || 0); if (leadMode === "capped") lead = Math.min(AGE, (opts.extrapCap || 0.6) * gap);
let nextDet = 0, errs = [], lags = [], xs = [];
for (let t = 0; t <= 12000; t += 1000 / FPS) {
  while (nextDet + AGE <= t) { const td = nextDet; const cx = truth(td); bt.ingest([{ cls: 'person', score: .9, id: 1, box: [cx - W / 2, 200, W, H] }], td, td + AGE); nextDet += gap; }
  const v = bt.sample(t + lead).filter(o => o.cls === 'person');
  if (v.length && t > 3000) { const dx = v[0].box[0] + W / 2; xs.push([t, dx]); errs.push(Math.abs(dx - truth(t))); }
}
// 시간 지연: 표시 x(t) 와 truth(t - d) 의 오차가 최소인 d
let best = null;
for (let d = -100; d <= 400; d += 5) { let e = 0; for (const [t, dx] of xs) e += Math.abs(dx - truth(t - d)); e /= xs.length; if (!best || e < best.e) best = { d, e }; }
errs.sort((a, b) => a - b);
console.log(JSON.stringify({ opts, leadMode, lead_ms: Math.round(lead), lag_ms: best.d, err_px_p50: +errs[Math.floor(errs.length / 2)].toFixed(1), err_px_p95: +errs[Math.floor(errs.length * .95)].toFixed(1) }));
