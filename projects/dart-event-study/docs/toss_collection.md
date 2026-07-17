# 토스 커뮤니티 순방향 수집 — 설계와 제약 준수

> 브랜치: `feat/nlp-signal`. 팍스넷 파이프라인과 별도 모듈(`src/dart_event_study/toss/`).
> 시작일: 2026-07-15. **과거 백필 불가 → 오늘부터 순방향으로만 쌓는다.**

## 왜 first-seen인가 (핵심 설계)

토스는 게시글에 **절대 시각을 주지 않는다.** 상대 시각("3시간 전")만 렌더하며 과거로 갈수록
"3일 전"·"1주 전"으로 뭉개져 장중/장후(15:30 경계) 분리가 불가능하다. 절대 시각은 WebSocket
경로에만 있을 수 있으나 그 관찰은 제약상 금지(WS 프레임 가로채기·프라이빗 API).

→ 그래서 **시각을 읽지 않고 우리가 찍는다.** 글이 처음 목록에 관측된 크롤 시각을
`first_seen_at`으로 기록해 사실상의 타임스탬프로 쓴다.

```
crawl_1(12:00) 목록에 없던 글이 crawl_2(15:30) 목록에 나타남
→ 그 글은 12:00~15:30 사이에 작성됨 → 장중
```

크롤을 장 경계에 맞추면 분리가 정확해진다:

| 크롤 슬롯 | 관측 창 | 귀속 |
|---|---|---|
| 09:00 | 전일 21:00 → 09:00 (야간) | 전일 **장후** |
| 12:00 | 09:00 → 12:00 | 당일 **장중** |
| 15:30 | 12:00 → 15:30 | 당일 **장중** |
| 21:00 | 15:30 → 21:00 | 당일 **장후** |

**주말·공휴일에도 계속 돌린다** — 연휴로 장후 창이 길어지는 것도 데이터다.
first_seen의 해상도는 크롤 간격(최대 반나절)이지만, 15:30 경계 판별에는 충분하다.

## 모듈 구성

| 파일 | 역할 |
|---|---|
| `toss/board.py` | Playwright+실제 Chromium으로 커뮤니티 열기 → **최신순 강제·단조성 검증** → 스크롤 파싱 |
| `toss/store.py` | 스냅샷 저장 + 누적 테이블(first_seen/last_seen/is_deleted) 갱신 |
| `toss/crawl.py` | 1회 크롤 CLI (스케줄러가 호출). 직전 관측 id 도달 시 조기 종료 |
| `toss/aggregate.py` | 일별 파생지표(장중/장후/삭제/좋아요·댓글) |
| `scripts/toss_crawl_run.ps1` | 크롤 실행 래퍼 + **실패 알림**(마커 파일 + 윈도우 토스트) |
| `scripts/toss_schedule_install.ps1` | Task Scheduler 등록(4슬롯+부팅, 재부팅 생존) |

## 저장 스키마

- **스냅샷**: `data/raw/toss/{code}/{crawl_ts}.parquet` — 매 크롤 관측 전체.
- **누적**: `data/raw/toss/{code}/_cumulative.parquet`
  `post_id, ticker, first_seen_at, last_seen_at, title, likes, comments,
   relative_time_label, author_hash, is_deleted, deleted_detected_at`
  - `first_seen_at` = 사실상의 타임스탬프.
  - `is_deleted` = 직전 크롤엔 있었는데 이번 관측창에서 사라진 글 → **삭제 신호**
    (스팸은 나중에 지워지는 경우가 많다. **삭제율 자체가 스팸 지표** — 팍스넷에선 불가능했던 것).
    - ⚠️ **삭제 판정은 현재 완전 비활성화(보류)** — is_deleted는 항상 False. 토스 post_id가
      전역 시간순이 아니고(실측: 겹침 0인 성긴 커버리지) 매 크롤이 id-공간에서 성기게
      관측하므로, `hit_stop` 게이트 + 관측 최소 id 경계로도 스캔 범위 안 미관측 글이 대량
      오탐된다(실측: 6,993건, 이후 10h 크롤서 또 4,793건 — 예 000660은 68글 관측에 736 오탐).
      정확한 삭제 탐지는 **피드 순서(스냅샷 row order) 기반**이라야 하며, 순방향 깨끗한 데이터
      축적 후 별도 태스크로 만든다. 현재 누적의 is_deleted는 `scripts/reset_deleted.py`로 리셋.
      (`store.update_cumulative`의 `hit_stop` 인자는 미래 탐지기용 시그니처로만 유지.)
  - `likes/comments` = 크롤마다 갱신(시계열은 스냅샷들에 보존).
- **일별**: `data/processed/toss_daily.parquet`
  `ticker, date, posts_intraday, posts_afterhours, posts_total, deleted_count, avg_likes, avg_comments`

## 최신순 게이트 (설계 전제)

인기순으로 수집하면 신규 글을 놓쳐 first-seen이 무너진다. 그래서 크롤마다 **최신순으로 전환하고**
아래 두 독립 증거 중 하나로 검증한다:
1. **상대시각 단조 증가** (표본 4+): 방금→N분→N시간 순.
2. **post_id 내림차순** (폴백): id는 시간순 발급(높을수록 최신)이라, 저활성 종목처럼 시각 표본이
   적어도 id 순서로 최신순을 확인. 첫 크롤서 알테오젠(시각표본 부족)이 1번만으론 실패 → 2번으로 해결.

검증 실패 시(글이 있는데 순서 확인 불가) 그 종목 크롤은 **실패 처리** — 잘못된 순서로 쌓지 않는다.
단 **빈 커뮤니티(글 0개)는 실패가 아니라 빈 결과**로 처리(비활성 종목이 매 크롤 거짓 알림 내는 것 방지).

**정렬 전환은 토글**(실측): 토스 정렬 컨트롤은 클릭할 때마다 인기순↔최신순으로 토글한다. 현재
라벨이 '인기순'일 때만 클릭해 '최신순'으로 바꾸고, 검증은 항상 실제 렌더된 글 순서로 한다.
(예전엔 드롭다운 '최신순' 항목을 좌표로 클릭하려 했으나 항목 탐지 필터가 안 맞아 실행 안 됐고,
실행됐다면 되레 정렬을 되돌렸을 것 — 제거함.) 정렬 실패로 건너뛴 종목은
`data/toss_logs/sort_failures.csv`에 `(crawl_ts, ticker, name, 초기 렌더 글 수)`로 결측 기록한다
(정렬 실패는 스냅샷 저장 전에 나므로 스냅샷이 없다 → 최소한의 결측 흔적).

## 첫 크롤 = 베이스라인 (first-seen 주의)

**첫 크롤의 first_seen_at은 진짜 작성시각이 아니다.** 관측된 글은 이미 존재하던 것(상대시각이
"1일 전"인 글도 포함)이라 first_seen=첫크롤시각은 실제 작성보다 늦게 찍힌다. 첫 크롤의 역할은
(a) 밀도·구조·정렬 검증, (b) "이미 본 글" 집합 확립뿐이다. **정확한 first_seen은 2번째 크롤부터** —
crawl_1에 없던 글이 crawl_2에 나타나면 그 사이에 작성된 것이 확실하다. 일별 집계의 첫날 값은
베이스라인 유입으로 부풀려져 있으니 분석에서 제외한다.

## 스케줄러 · 운영 리스크

- Windows Task Scheduler: 09:00/12:00/15:30/21:00 매일 + 부팅 시 1회(놓친 슬롯 보정),
  `StartWhenAvailable`로 절전 복귀 시 곧 실행. 등록: `scripts/toss_schedule_install.ps1`.
- **Playwright 브라우저 경로**(운영 블로커였음): Task Scheduler 실행 컨텍스트는
  `%LOCALAPPDATA%\ms-playwright`를 **읽지 못한다**(실측: 스케줄 프로세스에선 `chrome.exe`가
  `pathlib.exists()==False`, 대화형 셸에선 True — Defender 제어 폴더 접근/프로파일 컨텍스트 제약
  추정). 그래서 스케줄 슬롯이 전 종목 `Executable doesn't exist`로 실패했다. → 브라우저를
  **ASCII 고정 경로 `C:\pw-browsers`** 로 옮기고 래퍼가 `PLAYWRIGHT_BROWSERS_PATH`로 지정한다
  (스케줄 컨텍스트에서 읽힘 검증 완료). 브라우저 업데이트는 반드시 그 경로로:
  `$env:PLAYWRIGHT_BROWSERS_PATH="C:\pw-browsers"; uv run --project projects/dart-event-study python -m playwright install chromium`.
- **배터리·절전 대응**(운영 블로커였음): 기본 설정은 배터리 중 작업을 아예 시작하지 않아
  슬롯이 통째로 스킵된다. 그래서 `AllowStartIfOnBatteries`(배터리 시작 허용) +
  `DontStopIfGoingOnBatteries`(배터리 전환돼도 중단 안 함) + `WakeToRun`(절전에서 슬롯 시각에
  깨워 실행)을 명시한다. `ExecutionTimeLimit=3h`(야간 12h 공백 슬롯 대비),
  `MultipleInstances=IgnoreNew`(이전 슬롯 실행 중이면 새 인스턴스 안 띄움).
  - ⚠️ **한계**: `WakeToRun`은 절전(sleep)에서만 깨운다. **완전 종료(shutdown) 상태는 못 깨운다** —
    슬롯 시각엔 PC가 켜져 있거나 절전 상태여야 한다. 종료해두면 그 슬롯은 놓친다(백필 불가).
- **`AtStartup` 트리거는 관리자 권한 필요**: 4개 일일 슬롯은 비관리자로 등록되지만, 재부팅 복구용
  `AtStartup`은 관리자 실행이 필요하다. `scripts/toss_schedule_install.ps1`을 **관리자 PowerShell**로
  1회 실행하면 5개 트리거(4일일 + AtStartup) + 위 설정이 모두 반영된다. 등록 후
  `Get-ScheduledTask -TaskName QuantLab_TossCrawl`로 검증.
- **실패 알림이 최대 운영 리스크 대응**: 조용히 죽으면 며칠치를 날린다. 래퍼가 exit!=0 시
  `LAST_FAILURE.txt` 마커 + 윈도우 토스트를 남긴다. 로그는 `data/toss_logs/crawl_*.log`.
  (래퍼의 `$repo` 경로 계산이 한 단계 부족해 한동안 엉뚱한 경로에 쓰던 버그를 수정 — 3단계 상위.)

## 제약 준수 기록

- **실제 Chromium**(Playwright, UA 무변조). 토스는 크롬 지원 → 정식 이용. "미지원 브라우저"
  경고 없음 확인. 감지되면 우회 없이 중단.
- **렌더된 DOM만 읽음**. WS 프레임 가로채기·JS 번들 리버스·프라이빗/서명 API 호출 **없음**.
- robots.txt 준수(`*` Allow, `/_ul/`만 금지 — 커뮤니티 경로 허용). 차단·CAPTCHA 시 중단.
- **로그인 없음**. 작성자 닉네임·ID **원문 미저장** — salted hash(`author_hash`, 16자)만.
  salt는 `data/raw/toss/.salt`(gitignore, 커밋 안 함). 도배(동일인 반복) 탐지용.
- 원문 텍스트는 `data/raw/`(gitignore) 밖으로 나가지 않음. 종목 간 2.5초 간격.
