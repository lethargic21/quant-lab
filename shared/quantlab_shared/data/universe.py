"""KOSPI 유니버스 스냅샷.

⚠️ 두 겹의 근사가 있다 — 사용하는 프로젝트 README에 한계로 명시할 것:
1. KRX 정보데이터시스템(지수 구성종목 API)이 봇 차단이라 실제 KOSPI200
   구성종목을 프로그램으로 조회할 수 없음 → KOSPI 시총 상위 n 보통주로 근사.
2. 조회 시점 스냅샷이라 과거 편입/편출 이력 미반영 (survivorship bias).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def get_kospi_top_n(n: int = 200, cache_dir: Path | str | None = None) -> list[str]:
    """KOSPI 시가총액 상위 n 보통주 티커 — KOSPI200 대용 스냅샷.

    보통주 필터: 티커 끝자리 '0' (우선주는 5/7/K 등으로 끝남).
    캐시 파일에 조회일(fetched_at)을 기록해 스냅샷 시점을 남긴다.
    """
    cache_path = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"kospi_top{n}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text())["tickers"]

    import FinanceDataReader as fdr

    lst = fdr.StockListing("KOSPI")
    common = lst[lst["Code"].str.endswith("0")]
    tickers = common.sort_values("Marcap", ascending=False)["Code"].head(n).tolist()
    if cache_path is not None:
        cache_path.write_text(
            json.dumps({"fetched_at": dt.date.today().isoformat(), "tickers": tickers})
        )
    return tickers
