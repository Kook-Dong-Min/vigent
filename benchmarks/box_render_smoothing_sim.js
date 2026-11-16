// 라이브 기록(live_dets.json)을 허브와 같은 방식으로 BoxTracker 에 넣고 60fps 렌더를 시뮬레이션해 '매끄러움' 지표를 낸다.
'use strict';
const fs = require('fs'); const path = require('path');
const modPath = process.argv[3] || path.join(__dirname, '..', 'vigent-core', 'static', 'vigent-box-display.js');
const { BoxTracker } = require(modPath).VigentBoxDisplay;
const rec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const opts = JSON.parse(process.argv[4] || '{"extrapCap":0.85}');
const W = 640, H = 480, FPS = 60;
// 허브와 동일: ingest 는 수신 시각(recv)에, sample 은 now + lead(수신 나이 중앙값, 0~350 클램프)
const ages = rec.map(r => r.recv - r.ts).sort((a, b) => a - b); const lead = Math.max(0, Math.min(350, ages[Math.floor(ages.length / 2)]));
const bt = new BoxTracker(opts);
let t = rec[0].recv, i = 0, prev = null, steps = [], statics = 0, frames = 0, jumpsAtUpdate = []; const pend = [], settle = [];
const tEnd = rec[rec.length - 1].recv + 100;
while (t < tEnd) {
  let updated = false;
  while (i < rec.length && rec[i].recv <= t) {
    const dets = rec[i].dets.map(d => ({ cls: d.class, score: d.score, id: d.id == null ? -1 : d.id, box: [d.bbox[0] * W, d.bbox[1] * H, (d.bbox[2] - d.bbox[0]) * W, (d.bbox[3] - d.bbox[1]) * H] }));
    bt.ingest(dets, rec[i].ts, rec[i].recv); const pr = dets.find(d => d.cls === 'person'); if (pr) pend.push({ at: rec[i].recv, cx: pr.box[0] + pr.box[2] / 2, cy: pr.box[1] + pr.box[3] / 2 }); i++; updated = true;
  }
  const vis = bt.sample(t + lead).filter(v => v.cls === 'person');
  if (vis.length) {
    const b = vis[0].box; const cx = b[0] + b[2] / 2, cy = b[1] + b[3] / 2;
    if (prev) { const d = Math.hypot(cx - prev[0], cy - prev[1]) + Math.abs(b[2] - prev[2]) * 0.5; steps.push(d); if (d < 0.05) statics++; if (updated) jumpsAtUpdate.push(d); }
    for (let k = pend.length - 1; k >= 0; k--) { if (Math.hypot(cx - pend[k].cx, cy - pend[k].cy) <= 1.5) { settle.push(t - pend[k].at); pend.splice(k, 1); } }
    prev = [cx, cy, b[2]]; frames++;
  }
  t += 1000 / FPS;
}
steps.sort((a, b) => a - b); jumpsAtUpdate.sort((a, b) => a - b);
const q = (a, p) => a.length ? a[Math.min(a.length - 1, Math.floor(a.length * p))] : 0;
const mean = steps.reduce((s, x) => s + x, 0) / Math.max(steps.length, 1);
const varr = steps.reduce((s, x) => s + (x - mean) ** 2, 0) / Math.max(steps.length, 1);
settle.sort((a, b) => a - b);
console.log(JSON.stringify({ opts, lead_ms: Math.round(lead), settle_ms_p50: Math.round(q(settle, .5)), settle_ms_p90: Math.round(q(settle, .9)), unsettled: pend.length, frames, static_ratio: +(statics / Math.max(frames, 1)).toFixed(3), step_px_p50: +q(steps, .5).toFixed(2), step_px_p95: +q(steps, .95).toFixed(2), step_px_max: +q(steps, 1).toFixed(2), step_std: +Math.sqrt(varr).toFixed(2), jump_at_update_p50: +q(jumpsAtUpdate, .5).toFixed(2), jump_at_update_p95: +q(jumpsAtUpdate, .95).toFixed(2) }));
