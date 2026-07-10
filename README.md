# quant-lab

퀀트 리서치 프로젝트 모음. 이벤트 기반 시그널, 팩터 모델, 백테스트를
재현 가능한 형태로 쌓아가는 개인 리서치 랩.

방법론적 엄밀함(look-ahead 방지, 현실적 거래비용, 표본·한계 명시)을
수익률보다 우선한다.

## Projects

| Project | Description | Status |
|---------|-------------|--------|
| [`dart-event-study`](./projects/dart-event-study) | DART 공시 이벤트(자사주매입·유상증자·실적) → 시그널 → 이벤트 스터디 + 백테스트 (KOSPI200) | ✅ v1 |

## Stack

Python 3.11+ · pykrx / FinanceDataReader · OpenDART · uv

## Roadmap

- [x] DART 이벤트 스터디 (v1 — 자사주 드리프트만 비용 생존, 상세는 프로젝트 README)
- [ ] 이벤트에 대한 뉴스·개미 반응 감성 레이어
- [ ] 팩터 라이브러리 (밸류·모멘텀·퀄리티·로우볼)
