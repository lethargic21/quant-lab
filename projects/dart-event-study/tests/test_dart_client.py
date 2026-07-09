"""DartClient 페이지네이션·캐싱 검증 (네트워크 불필요 — _get 모킹)."""

from dart_event_study.dart.client import DartClient


def make_client(tmp_path, pages: dict[int, dict], counter: dict):
    client = DartClient("fake-key", cache_dir=tmp_path, min_interval=0)

    def fake_get(endpoint, **params):
        counter["calls"] += 1
        return pages[params["page_no"]]

    client._get = fake_get
    return client


PAGES = {
    1: {
        "status": "000",
        "total_page": 2,
        "list": [{"rcept_no": "20240101000001", "report_nm": "주요사항보고서(자기주식취득결정)"}],
    },
    2: {
        "status": "000",
        "total_page": 2,
        "list": [{"rcept_no": "20240102000002", "report_nm": "유상증자결정"}],
    },
}


def test_pagination_merges_all_pages(tmp_path):
    counter = {"calls": 0}
    client = make_client(tmp_path, PAGES, counter)
    rows = client.list_disclosures("00126380", "20240101", "20241231")
    assert [r["rcept_no"] for r in rows] == ["20240101000001", "20240102000002"]
    assert counter["calls"] == 2


def test_disk_cache_prevents_refetch(tmp_path):
    counter = {"calls": 0}
    make_client(tmp_path, PAGES, counter).list_disclosures("00126380", "20240101", "20241231")
    assert counter["calls"] == 2

    # 새 클라이언트 — 같은 캐시 디렉터리면 API 호출 0회
    counter2 = {"calls": 0}
    rows = make_client(tmp_path, PAGES, counter2).list_disclosures("00126380", "20240101", "20241231")
    assert counter2["calls"] == 0
    assert len(rows) == 2


def test_no_data_returns_empty(tmp_path):
    client = DartClient("fake-key", cache_dir=tmp_path, min_interval=0)
    client._get = lambda endpoint, **p: {"status": "013", "message": "조회된 데이터가 없습니다."}
    assert client.list_disclosures("00000000", "20240101", "20241231") == []
