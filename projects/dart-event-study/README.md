# dart-event-study

DART 전자공시에서 이벤트(자사주매입 / 유상증자 / 실적공시)를 감지해 구조화 정보를
추출하고, 이벤트 스터디와 백테스트로 검증한 엔드투엔드 파이프라인.
유니버스는 KOSPI 시총 상위 200(KOSPI200 근사), 기간 2019–2024.
설계 이력과 실측 기록은 [PLAN.md](./PLAN.md), 결과 상세는
[notebooks/report.ipynb](./notebooks/report.ipynb).

## 결과 요약 (정직 버전)

**1. 공시의 정보력은 실재한다** — 시장모형 이벤트 스터디 (표본: 자사주 174 / 실적 4,803 / 유증 101):

| 그룹 | [-1,+1] | [0,+5] | [0,+20] |
|---|---|---|---|
| 자사주+ (N=166) | +3.06% (t=8.1) | +3.75% (t=7.0) | **+4.68% (t=5.3)** |
| 실적+ (N=2,325) | +1.29% (t=11.6) | +0.63% (t=4.4) | n.s. |
| 실적− (N=1,664) | −0.60% (t=−4.8) | −0.89% (t=−5.6) | n.s. |
| 유증− (N=36) | n.s. | −3.33% (t=−2.6) | n.s. |

방향 룰의 부호가 세 이벤트 모두 실제 반응과 일치. 실적 반응은 5거래일 내 소화되고,
**자사주만 20거래일 드리프트가 유의**하다.

**2. 그러나 체결가능한 초과수익은 대부분 사라진다** — 익영업일 종가 진입 + 왕복 0.4% 비용:
- 자사주 H=5만 생존: net 연 +5.1%, Sharpe 0.21 (KOSPI +3.1%, 0.16) — 우위 미미, MDD −36%
- 실적 시그널: 회전율 44~56×/년 → gross 연 +14.4%(H=20 롱온리)가 net +0.7%로 붕괴
- 테스트한 조합(H=5/20 × long-short/only × 이벤트별) 전부 리포트함 — 좋은 것만 골라내지 않음

**핵심 발견은 수익률이 아니라 괴리의 정량화다**: 공시 정보는 하루 안에 가격에 반영되며,
look-ahead 없는 타이밍과 현실적 비용을 통과하면 자사주 드리프트만 간신히 남는다.

## 방법론

- **이벤트 추출**: OpenDART 구조화 API 우선 (`tsstkAqDecsn`, `piicDecsn`, `stockTotqySttus`),
  잠정실적만 공시 원문(`document.xml`) 표 파싱. 서프라이즈는 공시 안의 전년동기 수치로
  YoY 계산 (외부 컨센서스 불필요 → look-ahead 원천 차단), 결측 시 QoQ 폴백(`surprise_basis` 기록).
- **방향/강도 룰** ([config/settings.yaml](./config/settings.yaml)):
  자사주 = +1, 강도 = 취득예정주식수/발행주식총수(이벤트일 이전 공개된 정기보고서 기준).
  유증 = 배정방식 기본점수(주주배정·일반공모 −1, 제3자 0) + 자금용도 보정(채무상환 −0.5,
  시설·타법인취득 +0.5), 강도 = 희석률(신주/기존주식수 — 공시 필드만 사용).
- **타이밍 (look-ahead 방지)**: 접수시각이 데이터에 없어(실측) 전 건 **익영업일 종가 체결**의
  보수적 가정. 15:30 컷오프 장중/장후 분기 로직은 구현·테스트 완료 — 시각 소스 확보 시 활성화.
- **백테스트**: 동시 포지션 균등가중, |Δw| 기반 비용(거래세 0.20% 매도측 + 슬리피지 0.10% 편도),
  수정주가, KOSPI 벤치마크. 엔진은 [shared/backtest](../../shared/quantlab_shared/backtest).
- 유닛테스트 42개 (타이밍 경계, 파서, 방향룰, 시장모형, 엔진 — 전부 네트워크 불필요).

## 재현 방법

```bash
# 레포 루트에서
uv sync
cp .env.example .env          # OPENDART_API_KEY 입력 (https://opendart.fss.or.kr)

# 파이프라인 (모든 API 응답은 data/에 캐시 — 재실행 시 재크롤링 없음)
uv run --project projects/dart-event-study python -m dart_event_study.collect
uv run --project projects/dart-event-study python -m dart_event_study.events.extract
uv run --project projects/dart-event-study python -m dart_event_study.signals.build
uv run --project projects/dart-event-study python -m dart_event_study.analysis.event_study
uv run --project projects/dart-event-study python -m dart_event_study.analysis.backtest

# 결과 노트북
uv run --project projects/dart-event-study jupyter nbconvert --to notebook --execute --inplace \
  projects/dart-event-study/notebooks/report.ipynb
```

full 모드(200종목) 첫 실행은 API ~1.5만 호출 / 약 30분 (OpenDART 일한도 2만).
개발 시 [config/universe.yaml](./config/universe.yaml)에서 `mode: debug`(5종목)로 전환.

## 한계

- **유니버스**: 실제 KOSPI200이 아니라 KOSPI 시총 상위 200 보통주의 **현재 스냅샷**
  (KRX 지수구성종목 API가 봇 차단 — 2026-07 실측). 과거 편입/편출 미반영 → survivorship bias.
  급락·상폐 종목이 빠져 롱 수익률은 낙관, 숏은 비관 편향 가능.
- **접수시각 미확보**: OpenDART는 접수일자만 제공, DART 뷰어·KIND도 과거 시각 미제공(실측)
  → 익영업일 체결 가정. 당일 종가 체결 기회를 버리는 보수적 편향.
- **실적 이벤트 13.7% 방향 판정 불가** (2019~20 원문 미제공 4%p + 표 형식 변형).
- **거래정지/상폐**: 거래량 0 및 데이터 조기 종단 기반 추정 플래그만 — 정밀 이력 DB 미사용.
- **유상증자**: 방향성 시그널 58건 — 배정방식별 세분 시 표본 얇음. 숏 제약(대차 가능 여부·비용) 미반영.
- **컨센서스 부재**: 서프라이즈는 YoY(폴백 QoQ) 대용치 — 애널리스트 기대 대비가 아님.
- 일별 시가총액 무료 소스 부재 → 강도 지표는 주식수 기반 비율로 대체.

## Roadmap (범위 밖 — 코어 완성 후 검토)

- **감성 레이어 (2단계)**: 같은 자사주매입 공시라도 뉴스/커뮤니티 반응 감성이 뜨거운 종목의
  이후 수익률이 다른가 (과잉반응→되돌림 vs 모멘텀 지속). 소스 후보: 뉴스 기사 → 뉴스 댓글 →
  종목토론방. 자사주 드리프트가 유일한 생존 시그널이므로 자사주 이벤트부터 얹는 것이 자연스럽다.
- 접수시각 소스 확보 시 당일 체결 변형 백테스트 (현재 로직 내장·테스트 완료)
- 신탁계약 방식 자사주(`tsstkAqTrctrCnsDecsn`) 추가
