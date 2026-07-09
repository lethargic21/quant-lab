"""OpenDART API 클라이언트 — 스로틀링 + 디스크 캐싱.

OpenDART 한도: 일 20,000건, 과도한 연속 호출 시 차단 → 호출 간 최소 간격을 둔다.
모든 응답은 data/dart/ 아래 json 스냅샷으로 캐시되어 재실행 시 재크롤링하지 않는다.
"""

from __future__ import annotations

import io
import json
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import requests

BASE_URL = "https://opendart.fss.or.kr/api"

# OpenDART status 코드 중 "정상"과 "데이터 없음"만 통과, 나머지는 에러
STATUS_OK = "000"
STATUS_NO_DATA = "013"


class DartError(RuntimeError):
    pass


class DartClient:
    def __init__(self, api_key: str, cache_dir: Path | str, min_interval: float = 0.15):
        self.api_key = api_key
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self._last_call = 0.0
        self._session = requests.Session()

    # ── 저수준 ──────────────────────────────────────────────

    def _throttle(self) -> None:
        wait = self._last_call + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _get(self, endpoint: str, **params) -> dict:
        """json 엔드포인트 호출. status 000/013 외에는 DartError."""
        self._throttle()
        r = self._session.get(
            f"{BASE_URL}/{endpoint}", params={"crtfc_key": self.api_key, **params}, timeout=30
        )
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status not in (STATUS_OK, STATUS_NO_DATA):
            raise DartError(f"{endpoint} status={status}: {data.get('message')}")
        return data

    def _get_bytes(self, endpoint: str, **params) -> bytes:
        self._throttle()
        r = self._session.get(
            f"{BASE_URL}/{endpoint}", params={"crtfc_key": self.api_key, **params}, timeout=60
        )
        r.raise_for_status()
        return r.content

    def _cached_json(self, name: str, fetch) -> dict | list:
        path = self.cache_dir / f"{name}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        data = fetch()
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # ── 고수준 ──────────────────────────────────────────────

    def corp_code_map(self) -> dict[str, dict]:
        """{stock_code(6자리): {corp_code, corp_name}} — 상장사만.

        corpCode.xml(zip)을 받아 파싱, 캐시.
        """

        def fetch() -> dict:
            raw = self._get_bytes("corpCode.xml")
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                xml_bytes = zf.read(zf.namelist()[0])
            root = ET.fromstring(xml_bytes)
            out: dict[str, dict] = {}
            for el in root.iter("list"):
                stock_code = (el.findtext("stock_code") or "").strip()
                if len(stock_code) == 6:
                    out[stock_code] = {
                        "corp_code": el.findtext("corp_code").strip(),
                        "corp_name": (el.findtext("corp_name") or "").strip(),
                    }
            return out

        return self._cached_json("corp_codes", fetch)

    def list_disclosures(self, corp_code: str, bgn_de: str, end_de: str) -> list[dict]:
        """기간 내 공시 목록 전체 (list.json, 페이지네이션 처리).

        반환 필드(실측): corp_code, corp_name, stock_code, corp_cls,
        report_nm, rcept_no, flr_nm, rcept_dt(YYYYMMDD — 시각 없음), rm
        """

        def fetch() -> list[dict]:
            rows: list[dict] = []
            page_no, total_page = 1, 1
            while page_no <= total_page:
                data = self._get(
                    "list.json",
                    corp_code=corp_code,
                    bgn_de=bgn_de,
                    end_de=end_de,
                    page_no=page_no,
                    page_count=100,
                )
                if data.get("status") == STATUS_NO_DATA:
                    break
                rows.extend(data.get("list", []))
                total_page = int(data.get("total_page", 1))
                page_no += 1
            return rows

        return self._cached_json(f"list_{corp_code}_{bgn_de}_{end_de}", fetch)
