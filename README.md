# quant-lab

[![CI](https://github.com/lethargic21/quant-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/lethargic21/quant-lab/actions/workflows/ci.yml)

퀀트 리서치 프로젝트 모음. 이벤트 기반 시그널, 팩터 모델, 백테스트를
재현 가능한 형태로 쌓아가는 개인 리서치 랩.

방법론적 엄밀함(look-ahead 방지, 현실적 거래비용, 표본·한계 명시)을
수익률보다 우선한다.

## Projects

| Project | Description | Status |
|---------|-------------|--------|
| [`dart-event-study`](./projects/dart-event-study) | DART 공시 이벤트(자사주매입·유상증자·실적) → 시그널 → 이벤트 스터디 + 백테스트 (KOSPI200) | ✅ v1.2 |

## Stack

Python 3.11+ · FinanceDataReader · OpenDART · uv (workspace + `uv.lock` 고정)

CI: push/PR마다 `ruff` 린트 + `pytest`를 Ubuntu·Windows 양쪽에서 실행
([.github/workflows/ci.yml](.github/workflows/ci.yml)). 테스트는 네트워크·API 키 불필요.

## Roadmap

- [x] DART 이벤트 스터디 (v1 — 자사주 드리프트만 비용 생존, 상세는 프로젝트 README)
- [x] 이벤트 × 뉴스 어텐션 상호작용 (자사주+유증 3타입 단면 회귀 — 상호작용 null, 상세는 프로젝트 README)
- [ ] 종목토론방(토스) 어텐션 — 순방향 수집 중(2026-07~), 과거 이벤트 접합은 데이터 축적 후
- [ ] 팩터 라이브러리 (밸류·모멘텀·퀄리티·로우볼)
