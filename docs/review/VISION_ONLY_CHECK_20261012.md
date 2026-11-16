# 비전 전용(VIGENT_AGENTS=0) 실기 확인 — 2026-10-12 (④)

> ★⑤-5(2026-10-12) 에서 스위치 `VIGENT_AGENTS` 는 제거됐다 — 이제 **기본 기동이 곧 비전 전용**이다. 아래 기록의 "VIGENT_AGENTS=0" 은 당시 절차다.

> 대표가 Windows 개발기에서 `VIGENT_AGENTS=0` 으로 서버를 띄워 직접 확인한 결과와, 그때 나온 질문 4건의 조사 기록.
> 코드는 바꾸지 않았다. 근거는 `logs/vigent.log`·`data/recognition/events_20261012.jsonl`·코드 줄 번호이고, 추정은 [추정]으로 표시한다.
> 계획: [md/MERGE_PLAN_20261012.md](../../md/MERGE_PLAN_20261012.md) §4-1. **⑤(삭제)는 아래 §1 이 해결되기 전에는 시작하지 않는다.**

## 0. 정상 확인된 것 (대표 실기, 2026-10-12 03:0x)

- 기동: "에이전트·LLM 층 꺼짐(VIGENT_AGENTS=0)" 경고 · "경보 큐 DB: 운영" · 알림 채널 자가시험 통과 · 예열 완료 10.92 s
- `/safety-hub` 에서 영상 파일(`footage/*.mp4`)을 cam1 으로 등록 → 검출 박스 · 인원 5명 · 경보 상태 표시
- 텔레그램 채널 연결 시험 수신
- 카메라 삭제 후 재등록 정상 · 설정 콘솔 "■ 전체 중지" 정상(종료 깃발 안 섬)
- Ctrl+C 종료: 03:08:11.059~.062 에 계획된 순서(relay → 종료 깃발 → 워커 정리 → go2rtc)로 끝남, 워커 부활 없음

## 1. 남는 화면이 부르는 주소 중 꺼진 상태에서 404 인 것

남기기로 한 화면 5개(`/safety-hub`·`/safety-pro`·`/safety/setup`·`/dashboard`·`/login`)의 HTML·JS 에서 `fetch/api/href` 주소를 전부 뽑아 비전 전용 기준선(`baseline_openapi_vision.json`, 62개 작업)과 대조했다.

| 화면 | 호출 주소 | 종류 | 꺼진 상태 | 증상 | 해결 방법 |
|---|---|---|---|---|---|
| `/safety-hub` | `GET /safety/auto/feed?hours=24&limit=60` (2 s 마다, [index_hub.html:642](../../themes/safety/index_hub.html#L642)) | 이벤트 목록 | **404** | 경보가 나도 "이벤트 없음" | **허브 JS 1줄 교체**: `GET /recognition/log?hours=24&limit=60`(비전, [recognition.py:46](../../vigent-core/routers/recognition.py#L46)). 허브 `renderEvents` 가 쓰는 필드는 `ts·time·rule·level·site` 5개뿐이고 두 API 모두 같은 `data_engine.list_events` 레코드를 돌려준다. JS 는 이미 `j.feed||j.events` 를 받으므로 응답 키도 맞는다. 자동처리 피드만의 필드(`approved·ra_aid·law·advisory·pinned`)는 허브가 쓰지 않는다 |
| `/safety-pro` | `POST /rfdetr/vlm` (위험구역 안에 사람이 있고 쿨다운이 지나면 **자동 호출**, [index_rfdetr.html:162](../../themes/safety/index_rfdetr.html#L162)) | VLM 위험 요약 | **404** | 오른쪽 "AI 분석" 패널이 분석 중 → 실패로 반복, 콘솔 404 소음 | 호출 분기·패널 제거(⑤-3 의 "VLM 버튼 제거" 항목). 검출·위험구역(`/rfdetr/frame`·`/zone/danger`)은 영향 없음 |
| `/dashboard` | 상단 링크 `href="/home"`·`/safety/auto`(2)·`/safety/brain`·`/safety/reports`(2) | 이동 링크 | **404**(`/home` 은 캐치올 `/{theme}` 로 떨어져 "테마 페이지 없음" 404) | 죽은 링크 5개 | 링크를 `/safety-hub`·`/safety/setup` 으로 바꾸거나 제거(⑤-2 대시보드 축소 때) |
| `/safety-hub` | `location.href='/safety-local'` | 이동 링크 | OK(현재) | — | ⑤-3 에서 `/safety-local` 을 지우면 같이 지울 것 |
| `/safety/setup` | `/site/config`·`/notify/config`·`/workers`·`/workers/{start-all,stop-all}`·`/alerts/test` | — | 전부 OK | — | — |
| `/login` | `/login`·`/logout`·`/healthz`·`/static/*` | — | 전부 OK | — | — |

그 밖에 허브의 `/cameras*`·`/cameras/{id}/{detections,snapshot,focus,zone,test}`·`/health`·`/tapo/ws` 는 전부 비전 라우트라 OK 다.

**이런 누락을 잡는 정적 검사 (제안, 테스트 1건)**: `tests/test_vision_only_pages.py` — 남는 화면 파일 목록(위 5개 + 그 화면이 `<script src>` 로 싣는 `/static/*.js`)에서 정규식으로 `/…` 주소를 뽑아, `baseline_openapi_vision.json` 의 경로(`{param}` 은 와일드카드) + 정적 접두(`/static/`·`/evidence/`·`/tapo/ws`) 와 대조해 **하나라도 없으면 실패**. 이번 조사에 쓴 추출 스크립트가 그대로 뼈대다(세션 스크립트 `page_calls_off.py`). ⑤ 가 끝나 스위치가 사라져도 "화면이 부르는 주소는 전부 서버에 있다" 는 불변식으로 남긴다. 단, `href` 링크는 별도 패턴으로 잡아야 한다(이번 대시보드 사례).

## 2. 영상 파일 소스에서 확대 보기가 검은 바탕 + 박스만 나온 원인

- 허브 확대 보기는 먼저 **WebRTC**(go2rtc 경유 `/tapo/ws?src=cam1`)를 시도하고, 영상 폭이 0 보다 크면 그 모드로 고정해 스냅샷 `<img>` 를 숨긴다([index_hub.html:433](../../themes/safety/index_hub.html#L433)). 박스는 WebRTC 모드에서도 서버 `/cameras/{id}/detections` 를 폴링해 영상 위에 그린다(`spotStartDetect`, :458). 실패하면 3 s 간격 5회 뒤 스냅샷 모드로 떨어진다.
- 카메라를 등록·시작할 때 [cameras.py:70](../../vigent-core/routers/cameras.py#L70) 이 **등록부 source 문자열을 그대로** go2rtc 스트림으로 올린다. 파일 경로라도 `ffmpeg:` 접두·반복 재생 옵션 없이 올라간다(현재 코드에 `ffmpeg:` 변환 없음 — grep 0건. `data/go2rtc.log` 의 옛 기록에는 `ffmpeg:…mp4#video=h264` 로 올린 흔적과 그때도 파일 끝에서 `error=EOF` 가 난 줄이 있다).
- 따라서 증상은 **"WebRTC 가 성공으로 판정됐는데 영상은 검다"** 에 해당한다: 왼쪽 타일(스냅샷)은 워커의 마지막 프레임이라 정상이고, 오른쪽은 go2rtc 가 파일을 한 번 재생하고 끝낸(또는 첫 프레임을 못 보낸) 비디오 요소 위에 서버 박스만 올라간 상태다. 워커는 파일을 처음으로 되감아 계속 돌기 때문에 박스는 계속 갱신된다.
- [미검증] go2rtc 가 접두 없는 Windows 경로를 어떻게 여는지는 이번에 확인하지 못했다(서버가 꺼져 있고, 실행은 "조사만" 범위 밖). 확인법: 서버를 켜고 확대 보기를 연 상태에서 브라우저 콘솔의 `[VIGENT] WebRTC …` 경고 유무, 그리고 `curl http://127.0.0.1:1984/api/streams` 로 cam1 producer 상태를 본다.
- 고치는 방법(⑤ 와 무관, 별도 항목): 파일 소스는 go2rtc 에 등록하지 않고(`_g2_register` 에서 `Path(src).exists()` 면 건너뜀) 허브가 바로 스냅샷 모드를 쓰게 한다. 파일 소스는 벤치·시험용이라 실시간 WebRTC 가 필요 없다.

## 3. "전체 중지" 뒤 종료 로그의 `'stopped': 1` 해석

- [worker.py:1496](../../vigent-core/worker.py#L1496): `stop_all` 의 `stopped` = **등록된 워커 객체 중 `stop()` 이 ok 를 돌려준 수**이고 `total` = 워커 객체 수다. 이미 멈춘 워커도 `stop()` 이 `{"ok": True}` 를 돌려주므로([worker.py:889](../../vigent-core/worker.py#L889)) **"돌던 워커를 멈췄다" 는 뜻이 아니다.** "■ 전체 중지" 는 워커 객체를 지우지 않고 `running=False` 로만 두기 때문에 종료 때 `total: 1` 로 다시 센다.
- 로그 증거(03:07~03:08, `vigent.log` 에는 접근 로그가 없어 stop-all 시각은 직접 안 보인다):
  - 03:07:00·03:07:11 "카메라 '시험' 위험구역 미설정" 경고 2회 — 이 경고는 워커 루프가 시작될 때(`_setup_run`, [worker.py:1251](../../vigent-core/worker.py#L1251)) 한 번 나오므로 **루프 시작이 2번** 있었다(삭제→재등록 또는 재시작).
  - 인식 로그 `events_20261012.jsonl`: 03:00~03:07 매 분 4~14건씩 기록되다가 **마지막이 03:07:41**, 03:08 에는 0건. 종료는 03:08:11.
- 판정: 워커는 03:07:41 직후(전체 중지 시점[추정])부터 프레임을 처리하지 않았고, **종료 시점에 실제로 돌고 있던 워커는 없었다**[추정 — 30 초 공백은 규칙별 쿨다운 15 s 보다 길어 "돌고 있었다면 기록이 있었을" 가능성이 높지만 확정은 아니다]. 종료 로그의 `stopped: 1` 은 "이미 멈춘 워커 1개를 다시 stop 해 ok" 를 센 것이다. **즉 이번 실기는 "워커가 도는 중의 Ctrl+C" 를 검증한 것이 아니다.** 그 항목은 재등록 뒤 전체 중지를 누르지 않은 상태에서 다시 해야 한다.
- 기록 개선 제안(코드 변경, 별도 승인): `stop_all` 반환에 `was_running`(정지 전 running 이던 수)을 추가하고 종료 로그에 같이 찍으면 이 혼동이 사라진다.

## 4. `data/cameras.json` 에 남은 cam1 — 서버를 켜면 자동으로 다시 시작되는가

**예, 시작된다.** 현재 등록부는 cam1(이름 '시험', `enabled: true`) 1건이다[실측]. 기동 순서는 예열 완료 → `on_ready` → [cameras.py:461](../../vigent-core/routers/cameras.py#L461) `autostart_enabled()` 가 등록부의 `enabled` 카메라를 전부 `_start` 한다. 로그에는 "카메라 자동복원 → {'cam1': 'started'}" 로 찍힌다. 지우지 않으면 다음 기동 때 시험 영상 워커가 다시 돌고 go2rtc 등록도 다시 된다. 허브의 "삭제" 를 누르면 등록부·비밀 파일·워커·go2rtc 등록이 함께 지워진다(`DELETE /cameras/cam1`).

## 5. 조사 중 함께 발견한 것 (수정 없음)

- **`data/go2rtc.log` 에 RTSP 자격증명이 평문으로 남는다.** go2rtc 자체 로그가 producer 오류를 찍을 때 `url=rtsp://아이디:비번@…` 을 그대로 쓴다. `vigent.log` 는 마스킹하지만 이 파일은 go2rtc 가 직접 쓴다. 보존 스윕 D 그룹(`ops_log_max_mb`)의 회전 대상이긴 하나 내용은 그대로다. → OPEN_ISSUES 에 보안 항목으로 올릴 것(값은 이 문서에 적지 않았다).
- 이번 실기 정답표(인원 5명·경보)는 화면 관찰이며 박스 좌표 정답은 없다. 야드 영상의 사람 검출 재현율은 2026-10-12 낮에 보고한 조사(person 슬롯 COCO Nano·384·0.40)의 측정 계획대로 정답을 만들어야 잴 수 있다.

## 6. ⑤ 전 처리 순서(제안)

1. §1 허브 이벤트 목록 API 교체(JS 1줄) + 정적 검사 테스트 추가 → 게이트 → 커밋. 이것이 끝나야 ⑤ 시작.
2. §3 "워커가 도는 중의 Ctrl+C" 재실기(전체 중지 없이 바로 종료) — 대표.
3. §2 파일 소스 go2rtc 미등록(선택, 벤치 편의) · §5 go2rtc 로그 자격증명 — OPEN_ISSUES 항목으로 등록.
