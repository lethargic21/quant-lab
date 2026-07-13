"""1단계 규칙 베이스라인 적용 + 2단계 라벨링 샘플 생성 (네트워크 불필요).

실행:  uv run python -m dart_event_study.board.baseline
- data/raw/paxnet/{code}_posts.parquet → 규칙 플래그 → data/processed/{code}_labeled.parquet
- 커버리지(규칙별 발화율) 출력 — ⚠️ 사람 라벨 전이므로 '정확도'가 아니라 '잡은 비율'일 뿐
- 무작위 300개 블라인드 샘플 → labels/spam_labels_300.csv (규칙 플래그 미포함 — 라벨링 편향 방지)
"""

from __future__ import annotations

import pandas as pd

from dart_event_study.board.collect import TICKERS
from dart_event_study.board.spam_rules import SpamRuleParams, add_rule_flags
from dart_event_study.config import DATA_DIR, PROJECT_ROOT

SEED = 42
N_SAMPLE = 300
FLAGS = ["flag_marker", "flag_link", "flag_contact", "flag_lead", "flag_dup", "flag_burst"]


def main() -> None:
    raw_dir = DATA_DIR / "raw" / "paxnet"
    proc_dir = DATA_DIR / "processed"
    proc_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    for code in TICKERS:
        df = pd.read_parquet(raw_dir / f"{code}_posts.parquet")
        if df.empty:
            print(f"{code}: 데이터 없음 — 건너뜀")
            continue
        frames.append(df)
    all_posts = pd.concat(frames, ignore_index=True)

    params = SpamRuleParams()
    flagged = add_rule_flags(all_posts, params)
    for code, g in flagged.groupby("ticker"):
        g.to_parquet(proc_dir / f"{code}_labeled.parquet")

    n = len(flagged)
    print(f"총 {n:,}글 ({len(frames)}종목) | 규칙 파라미터: {params}")
    print("\n규칙별 커버리지 (사람 라벨 전 — 발화율일 뿐, 정확도 아님):")
    for f in FLAGS:
        print(f"  {f:14s}: {flagged[f].sum():5d}건 ({flagged[f].mean():.1%})")
    print(f"  {'spam_rule(OR)':14s}: {flagged['spam_rule'].sum():5d}건 ({flagged['spam_rule'].mean():.1%})")
    print("\n종목별 spam_rule 비율:")
    print(flagged.groupby("ticker")["spam_rule"].mean().map("{:.1%}".format).to_string())

    # 규칙이 잡은 것 눈검사용 샘플 (제목 30자)
    print("\n[눈검사] spam_rule=True 무작위 10건:")
    for t in flagged[flagged.spam_rule].sample(min(10, int(flagged.spam_rule.sum())), random_state=SEED)["title"]:
        print("  -", t[:60])

    # 2단계: 블라인드 라벨링 샘플 (규칙 플래그 제외 — 순환 편향 방지)
    labels_dir = PROJECT_ROOT / "labels"
    labels_dir.mkdir(exist_ok=True)
    sample = all_posts.sample(N_SAMPLE, random_state=SEED).sort_values("posted_at")
    out = sample.assign(post_id=sample["ticker"] + "_" + sample["seq"], label="", note="")[
        ["post_id", "ticker", "posted_at", "title", "label", "note"]
    ]
    path = labels_dir / "spam_labels_300.csv"
    out.to_csv(path, index=False, encoding="utf-8-sig")  # 엑셀 호환
    print(f"\n라벨링 샘플 저장: {path} ({len(out)}건, label 컬럼 비어 있음)")
    print("→ docs/spam_labeling_guide.md 기준으로 label 컬럼에 spam/ham 직접 기입 후 알려주세요.")


if __name__ == "__main__":
    main()
