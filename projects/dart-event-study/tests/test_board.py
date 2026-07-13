"""게시판 수집 파서 + 스팸 규칙 검증 (네트워크 불필요)."""

import datetime as dt

import pandas as pd
from dart_event_study.board.paxnet import parse_kst, parse_list_page
from dart_event_study.board.spam_rules import SpamRuleParams, add_rule_flags, normalize

# 정찰 실측 마크업 축약 (작성자 div.write 포함 — 파서가 읽지 않아야 함)
FIXTURE = """
<li><div class="type type_" data-seq="111">148329</div>
<div class="title"><p class="tit"><a class="best-title" href="javascript:bbsWrtView(111);">● 속보) 삼성전자 매수 적기</a>
<a href="#"><b class="comment-num" id="comment-num_111">2</b></a></p></div>
<div class="write"><a href="javascript:viewProfile('secret_user')">닉네임노출</a></div>
<div class="viewer" id="hitsNum_111"><span>조회 </span>7</div>
<div class="like" id="recmNum_111"><span>추천 </span>1</div>
<div class="date"><span class="data-date-format" data-date-format="Mon Jul 13 12:47:09 KST 2026"></span></div></li>
"""


def test_parse_kst():
    assert parse_kst("Mon Jul 13 12:47:09 KST 2026") == dt.datetime(2026, 7, 13, 12, 47, 9)
    assert parse_kst("잘못된 형식") is None


def test_parse_list_page_fields_and_no_author():
    rows = parse_list_page(FIXTURE)
    assert len(rows) == 1
    r = rows[0]
    assert r["seq"] == "111"
    assert r["title"] == "● 속보) 삼성전자 매수 적기"
    assert r["posted_at"] == dt.datetime(2026, 7, 13, 12, 47, 9)
    assert (r["views"], r["likes"], r["n_comments"]) == (7, 1, 2)
    # 작성자 정보는 어떤 필드에도 없어야 함
    assert not any("secret_user" in str(v) or "닉네임" in str(v) for v in r.values())


def test_normalize_collapses_variants():
    assert normalize("무료체험!! 123") == normalize("무 료 체 험 999")
    assert normalize("삼성전자 간다") != normalize("삼성전자 안 간다")


def make_df(titles_times):
    return pd.DataFrame(
        [{"ticker": "005930", "title": t, "posted_at": ts} for t, ts in titles_times]
    )


def test_marker_link_contact_lead_flags():
    base = dt.datetime(2026, 7, 13, 10, 0, 0)
    df = make_df([
        ("● 속보) 무조건 상한가", base),
        ("자세한 분석은 www.example.com 참고", base),
        ("오픈채팅 입장코드 1234", base),
        ("VIP 리딩방 무료체험 선착순", base),
        ("오늘 실적 좋네요", base),
    ])
    out = add_rule_flags(df)
    assert out["flag_marker"].tolist() == [True, False, False, False, False]
    assert out["flag_link"].tolist() == [False, True, False, False, False]
    assert out["flag_contact"].tolist() == [False, False, True, False, False]
    assert out["flag_lead"].tolist() == [False, False, True, True, False]  # 입장코드도 lead
    assert out["spam_rule"].tolist() == [True, True, True, True, False]


def test_dup_and_burst():
    base = dt.datetime(2026, 7, 13, 10, 0, 0)
    df = make_df(
        [("내일 갑니다 가즈아", base + dt.timedelta(minutes=i * 5)) for i in range(3)]  # 15분 내 3회
        + [("차분한 분석글입니다", base), ("차분한 분석글입니다", base + dt.timedelta(days=30))]  # 2회, 멀리
    )
    out = add_rule_flags(df, SpamRuleParams(dup_min=3, burst_window_min=60, burst_min=2))
    assert out["flag_dup"].tolist()[:3] == [True, True, True]
    assert out["flag_burst"].tolist()[:3] == [True, True, True]
    assert out["flag_dup"].tolist()[3:] == [False, False]  # 2회 < dup_min
    assert out["flag_burst"].tolist()[3:] == [False, False]  # 창 밖


def test_short_titles_excluded_from_dup():
    base = dt.datetime(2026, 7, 13, 10, 0, 0)
    df = make_df([("ㅋㅋ", base + dt.timedelta(minutes=i)) for i in range(5)])
    out = add_rule_flags(df)
    assert not out["flag_dup"].any() and not out["flag_burst"].any()


def test_disabled_returns_all_false():
    df = make_df([("VIP 리딩방 무료체험", dt.datetime(2026, 7, 13))])
    out = add_rule_flags(df, SpamRuleParams(enabled=False))
    assert not out["spam_rule"].any()
