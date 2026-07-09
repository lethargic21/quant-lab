# dart-event-study — 실행 계획 (PLAN)

> DART 전자공시 이벤트(자사주매입 / 유상증자 / 실적공시)에서 구조화 정보를 추출하고,
> 이벤트 스터디 + 백테스트로 검증하는 엔드투엔드 파이프라인.
> 가치 기준: 수익률이 아니라 **파이프라인 설계 + 방법론적 엄밀함** (look-ahead 방지,
> 현실적 비용, 표본·한계 명시). 결과가 나빠도 그대로 리포트한다.

---

## 0. 스코프 요약

| 항목 | 값 |
|------|-----|
| 유니버스 | KOSPI200 근사(시총 상위 200 보통주 스냅샷 — 아래 §7 참조), config로 축소 가능 |
| 기간 | 2019-01-01 ~ 2024-12-31 (config) |
| 이벤트 | 자사주매입 / 유상증자 / 실적공시 3종 |
| 데이터 | OpenDART API(공시), FinanceDataReader/네이버(수정주가 — pykrx는 KRX 봇차단으로 불가, §7) |
| 스택 | Python 3.11+, uv workspace, 랜덤시드 고정 |
| 범위 밖 | 뉴스/커뮤니티 감성 (README Roadmap에만 기재, 구현 안 함) |

---

## 1. uv workspace 셋업 계획 (Phase 0)

루트 `pyproject.toml`을 uv workspace 루트로 만들고, `shared/`와
`projects/dart-event-study/`를 멤버로 등록한다. 루트 README.md는 건드리지 않는다.

```
quant-lab/
├── pyproject.toml            # workspace 루트 (패키지 아님, 멤버 선언만)
├── uv.lock                   # 워크스페이스 공용 락파일
├── .env.example              # OPENDART_API_KEY=...
├── .gitignore                # .env, data/, __pycache__, .venv 등
├── shared/
│   └── pyproject.toml        # 패키지명: quantlab-shared
└── projects/dart-event-study/
    └── pyproject.toml        # 패키지명: dart-event-study, quantlab-shared를 workspace 의존성으로
```

- 루트 `pyproject.toml`:
  ```toml
  [tool.uv.workspace]
  members = ["shared", "projects/*"]
  ```
- `dart-event-study`가 `quantlab-shared`를 의존성으로 갖고,
  `[tool.uv.sources] quantlab-shared = { workspace = true }`로 연결.
- 의존성(초안): `requests`, `pandas`, `pykrx`, `finance-datareader`,
  `python-dotenv`, `pyarrow`(캐시), `scipy`(유의성 검정), `matplotlib`,
  dev: `pytest`, `ruff`.
- 데이터 캐시는 `projects/dart-event-study/data/` (gitignore, parquet/json 스냅샷).

---

## 2. 목표 구조 (스캐폴드 완료)

```
projects/dart-event-study/
├── README.md            # 방법론·결과·한계·재현 방법 (Phase 5에서 완성)
├── PLAN.md              # 이 문서
├── config/              # universe.yaml(KOSPI200 + 디버그 서브셋), settings.yaml(기간·비용·방향룰)
├── src/dart_event_study/        # src-layout 패키지 (import dart_event_study.*)
│   ├── dart/            # OpenDART 클라이언트 + 레이트리밋 + 디스크 캐싱
│   ├── events/          # 이벤트 감지 + 구조화 정보추출 (자사주/유증/실적)
│   ├── signals/         # 이벤트 → 시그널 변환 + 체결가능시점 매핑
│   └── analysis/        # 이벤트 스터디 통계 (AAR/CAR, 유의성) — 결정 3
├── notebooks/           # 재현용 분석 노트북 (이벤트 스터디, 백테스트 결과)
├── tests/               # 타이밍 로직·데이터 로더 유닛테스트
└── data/                # (gitignore) API 응답 스냅샷 캐시

shared/
├── pyproject.toml       # 패키지명 quantlab-shared
└── quantlab_shared/     # import quantlab_shared.*
    ├── data/            # 가격/수정주가 로더, 영업일 캘린더, 거래정지/상폐 플래그
    └── backtest/        # 백테스트 엔진, 성과지표(Sharpe/Sortino/MDD/turnover/hit), 거래비용 모델
```

(임포트 가능한 정식 패키지가 되도록 `shared/quantlab_shared/`, `src/dart_event_study/`
한 단계를 추가 — 개념 구조는 원안과 동일.)

### shared / 프로젝트 분리 기준

| 위치 | 내용 | 근거 |
|------|------|------|
| `shared/data` | 수정주가 로더(pykrx/FDR 래퍼 + 캐싱), KRX 영업일 캘린더, 거래정지·상폐 플래그 | 팩터 라이브러리에서도 그대로 씀 |
| `shared/backtest` | 포트폴리오 백테스트 엔진, 성과지표, 거래비용 모델(거래세+슬리피지 파라미터) | 범용 |
| 프로젝트 `src/` | DART 클라이언트, 이벤트 감지/추출, 방향·강도 룰, 타이밍 매핑 | 공시 특화 |
| **애매(질문)** | 이벤트 스터디 통계(AAR/CAR, t-검정) — 범용성 있지만 이번엔 여기서만 씀 | 사용자 결정 |

---

## 3. 이벤트별 처리 설계

이벤트 레코드 공통 스키마:

```
(ticker, corp_code, rcept_no, rcept_datetime, event_type, features..., direction, strength)
```

### 접근 원칙: 구조화 엔드포인트 우선, 본문 파싱은 보완

OpenDART **주요사항보고서 API 그룹**에 이벤트별 구조화 엔드포인트가 있다.
Phase 2 시작 시 실제 응답을 찍어 필드 존재를 확인한 뒤 확정한다(문서상 후보):

- 자사주: `tsstkAqDecsn`(자기주식 취득 결정) — 취득예정금액/수량, 취득방법, 목적
- 유상증자: `piicDecsn`(유상증자 결정) — 신주 수, **증자방식(배정방식)**, 자금조달 목적별 금액(시설/운영/채무상환/타법인증권취득 등)
- 실적: 잠정실적은 구조화 API가 없어 공시검색(`list.json`)에서
  "영업(잠정)실적" 공시를 잡고, 본문 파싱 또는 정기보고서 재무 API(`fnlttSinglAcnt`)로 보완

⚠️ 위 엔드포인트/필드명은 **실제 응답으로 검증 후 사용**. 없으면 본문 파싱으로 대체하고 그 사실을 기록.

### 3.1 자사주매입 (+)
- 추출: 취득예정금액, 취득방법(직접/신탁), 목적
- strength = 취득예정금액 / 공시 전일 시가총액
- direction = +1 (롱 후보)

### 3.2 유상증자 (방향 분기 — 텍스트/구조화 분류 필요 지점)
- 추출: 배정방식(주주배정 / 주주배정후실권주일반공모 / 제3자배정 / 일반공모),
  자금 목적(시설/운영/채무상환/타법인취득), 발행규모 → 시총 대비 희석률
- 방향 룰 (사용자 초안, config로 조정 가능):

  | 배정방식 | 기본 방향 |
  |----------|-----------|
  | 주주배정 / 일반공모 | 악재 (−) |
  | 제3자배정 | 중립 ~ 호재 |
  + 목적 보정: 채무상환 = 약한 악재, 시설·타법인취득 = 중립~약호재
- strength = 희석률 기반
- 분류는 룰/키워드 + 구조화 필드 우선. 부족하면 한국어 모델 도입을 **별도 제안**(임의 도입 금지)
- ⚠️ 표본 수 반드시 리포트, 얇으면 통계 결론 유보

### 3.3 실적공시 (서프라이즈)
- 추출: 매출/영업이익/순이익
- 서프라이즈 = 실제 vs 컨센서스. 무료 컨센서스 확보가 어려우면 **YoY/QoQ 성장률 대용** + 명시
- direction = sign(서프라이즈), strength = 크기

---

## 4. 절대 원칙 (구현에 반영되는 위치)

1. **Look-ahead 금지**: 시그널은 `rcept_dt`/`rcept_no` 접수시각 이후만 사용.
   장중(≤15:30) 공시 → 당일 종가 체결 가능, 장 마감 후 → 익영업일.
   `src/signals/timing.py`에 구현, `tests/test_timing.py`로 검증 (경계: 15:29/15:30/15:31, 금요일 장후→월요일, 공휴일).
2. **수정주가** 사용 (`shared/data` 로더에서 보장).
3. **Survivorship**: KOSPI200 구성이 시점별로 다름 — 처리 방식 한계를 README에 명시 (아래 열린 질문 1).
4. **거래비용**: 거래세(기본 0.20%, 파라미터) + 슬리피지(파라미터) — `shared/backtest/costs.py`.
5. **다중검정**: 테스트한 윈도우/파라미터 전부 리포트. 좋은 것만 골라 자랑 금지.
6. **과설계 금지**: MVP 우선, 단순·해석 가능.

---

## 5. Phase 계획 (각 phase 끝 = 커밋 + 요약 + 진행 확인)

| Phase | 내용 | 산출물 |
|-------|------|--------|
| 0 | uv workspace, 스캐폴드, config, `.env.example`, README 뼈대 | 실행 가능한 빈 패키지 2개 |
| 1 | OpenDART 공시 수집(접수시각 포함) + 캐싱, 가격 로더(shared), 거래정지/상폐 플래그 | `data/` 스냅샷, 로더 테스트 |
| 2 | 이벤트 3종 감지 + 정보추출 (구조화 API 응답 실증 → 파서) | 이벤트 테이블 (스키마 위 참조) |
| 3 | 타이밍 매핑 + 방향/강도 룰 → 시그널 | 시그널 테이블 + 타이밍 유닛테스트 |
| 4 | (a) 이벤트 스터디: AAR/CAR, 윈도우별 유의성 (b) 백테스트: 비용 반영, KOSPI 벤치마크 | 결과 테이블/차트 |
| 5 | 리포트 노트북 + 프로젝트 README 완성, 루트 README 상태 갱신 **제안** | 최종 리포트 |

---

## 6. 확정된 설계 결정 (2026-07-09)

1. **KOSPI200 유니버스**: pykrx `get_index_portfolio_deposit_file("1028")` **현재 구성 스냅샷** 사용.
   과거 편입/편출 이력 미반영 → survivorship bias 한계를 README에 명시.
2. **디버그 서브셋 (5종목)**: 삼성전자 005930, 셀트리온 068270, 카카오 035720,
   한화솔루션 009830, 대한항공 003490 (뒤 두 개는 2020~21 대형 유상증자 표본용).
   config에서 `debug` ↔ `full` 모드 전환.
3. **이벤트 스터디 통계(AAR/CAR)**: 프로젝트 `src/` 내부(`analysis/`)에 둠.
   다른 프로젝트에서 실사용처가 생기면 그때 `shared/`로 승격 (YAGNI).
4. **실적 서프라이즈 대용치**: 무료 컨센서스 부재 시 **YoY + QoQ 병행 계산·리포트**,
   시그널 방향은 YoY 기준. 대용치 사용 사실을 리포트에 명시.

---

## 7. Phase 1 실측으로 확정된 변경 (2026-07-09)

1. **가격 소스: pykrx → FinanceDataReader(네이버)**.
   KRX 정보데이터시스템이 세션 쿠키·브라우저 헤더를 갖춰도 400 "LOGOUT"으로
   봇 차단 → pykrx(및 FDR SnapDataReader의 KRX 경로) 사용 불가.
   FDR 네이버 소스는 수정주가 확인됨 (카카오 2021-04 5:1 분할 전후 연속성 실측).
2. **KOSPI200 → KOSPI 시총 상위 200 보통주 근사**.
   지수 구성종목 API가 위 차단에 포함 → `fdr.StockListing("KOSPI")` 시총 상위
   200(티커 끝자리 '0' = 보통주)로 근사. 스냅샷 조회일을 캐시에 기록.
3. **시그널 강도: 시총 대비 비율 → 주식수 기반 비율**.
   일별 시가총액 무료 소스가 없어짐 → 자사주 강도 = 취득예정주식수/발행주식총수,
   유증 희석률 = 신주수/증자전 발행주식총수. 공시 안의 필드만 쓰므로
   look-ahead 관점에서도 더 안전. (Phase 2에서 필드 존재 실증 후 최종 확정)
4. **`rcept_dt`에는 시각이 없음 (실측)**. list.json은 접수'일자'만 제공.
   → Phase 3에서 이벤트 건에 한해 DART 뷰어 페이지의 접수시각을 보완 시도,
   실패 시 전 건 "익영업일 체결"의 보수적 가정으로 후퇴 (look-ahead 없는 방향).
5. **거래정지/상폐 플래그 동작 확인**: volume=0 기반 플래그가 카카오 분할 정지 3일,
   한화솔루션 23일을 실제로 잡음. 상폐 추정은 데이터 조기 종료(20거래일+) 기준.

---

## 8. Phase 2 실측으로 확정된 사항 (2026-07-09)

1. **구조화 엔드포인트 실증 완료**: `tsstkAqDecsn`(자사주 — 취득예정주식수/금액/방법/목적),
   `piicDecsn`(유증 — 신주수/증자전총수/배정방식/용도별금액), `stockTotqySttus`(발행주식총수).
   유증 희석률은 공시 필드만으로 계산됨 (외부 데이터 불필요).
2. **자사주 강도 정규화**: 발행주식총수는 이벤트일 이전에 '공개된' 최신 정기보고서
   (기말+45일/사업보고서+90일 보수적 공개 가정 — look-ahead 안전 방향)의 보통주 총수.
3. **실적 서프라이즈는 공시 원문에서 직접**: 잠정실적 표에 당해/전기/전년동기 실적이
   함께 실림 → YoY·QoQ를 원수치로 계산 (외부 컨센서스·look-ahead 불필요).
   구형 공시(주로 2019~20)는 전년동기 칸이 비어 있음 → QoQ 폴백, `surprise_basis`에 기록.
4. **document.xml 인코딩 함정 (실측)**: 구형 문서 = euc-kr, 2022+ 문서 = *meta는 euc-kr로
   선언했지만 실제는 utf-8*. utf-8 strict 우선 → 실패 시 cp949 폴백으로 해결.
5. **커버리지 한계**: 2019~20년 잠정실적 9/210건(4.3%)은 document.xml API가 문서를
   제공하지 않음(에러 XML 반환) → 방향 판정 불가로 남김. 리포트에 명시.
6. **신탁 자사주**: 디버그 셋 표본 0건 → 직접취득만 구현. full 실행에서 표본 확인 후
   `tsstkAqTrctrCnsDecsn` 추가 검토.
7. **디버그 셋 이벤트 표본**: 자사주 22 / 유증 5 / 실적 210 (판정가능 201).
   추출값 정합성 실측 확인: 대한항공 2020·21 유증 희석률 84%·100%(실제 초대형 증자),
   한화솔루션 21년 19.7%, 자사주 강도 0.2~1.7% 범위.
