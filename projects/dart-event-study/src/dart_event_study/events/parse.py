"""DART 응답/원문 공통 숫자 파싱."""

from __future__ import annotations


def num(s: str | None) -> float | None:
    """DART 숫자 문자열 → float. '-'/빈값 → None, '△'/괄호 = 음수, 콤마 제거."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if s in ("", "-"):
        return None
    if s.startswith("△"):
        s = "-" + s[1:]
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def growth(cur: float | None, base: float | None) -> float | None:
    """성장률 (cur - base) / |base|.

    분모에 절대값을 써서 적자 구간에서도 방향이 직관과 일치:
    적자 축소/흑자 전환 = +, 적자 확대/적자 전환 = −.
    """
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / abs(base)
