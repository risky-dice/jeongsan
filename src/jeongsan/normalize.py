"""표기 정규화 — 금액 / 날짜 / 사업자등록번호 / 업체명.

OCR·한글 문서에서 나오는 온갖 표기를 하나로 모은다.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Optional

__all__ = [
    "parse_money", "parse_date", "normalize_biz_no", "valid_biz_no",
    "normalize_vendor_name", "format_money", "korean_amount", "round_won",
]

_ROUNDING = {"half_up": "ROUND_HALF_UP", "floor": "ROUND_FLOOR",
             "ceil": "ROUND_CEILING"}


def round_won(value, mode: str = "half_up") -> int:
    """원 단위 정수로 맞춘다.

    파이썬 기본 :func:`round` 는 은행가 반올림(round-half-even)이라
    100000.5 → 100000 이 된다. 회계에서는 이 동작을 기대하지 않으므로
    쓰지 않는다.

    mode
        ``half_up``  0.5 올림 (수량×단가 등 일반 계산)
        ``floor``    원 단위 절사 (부가세 관행)
        ``ceil``     올림
    """
    from decimal import Decimal, getcontext  # noqa: F401  (지연 import)
    import decimal
    if mode not in _ROUNDING:
        raise ValueError(f"알 수 없는 반올림 방식: {mode}")
    d = decimal.Decimal(str(value))
    return int(d.quantize(decimal.Decimal("1"),
                          rounding=getattr(decimal, _ROUNDING[mode])))

_HANGUL_DIGIT = {"영": 0, "일": 1, "이": 2, "삼": 3, "사": 4,
                 "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
_HANGUL_UNIT = {"십": 10, "백": 100, "천": 1000}
_HANGUL_BIG = {"만": 10**4, "억": 10**8, "조": 10**12}


def parse_money(value) -> Optional[int]:
    """'1,234,000원' '\\1,234,000' '금 3,322,000원' '삼백삼십이만이천' → 3322000"""
    if value is None:
        return None
    if isinstance(value, (int,)):
        return int(value)
    if isinstance(value, float):
        return round(value)
    s = unicodedata.normalize("NFKC", str(value)).strip()
    if not s:
        return None
    # 한글 금액 (숫자가 전혀 없을 때만 시도)
    if not re.search(r"\d", s):
        return _parse_korean_money(s)
    s = s.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    return round(float(m.group()))


def _parse_korean_money(s: str) -> Optional[int]:
    """'삼백삼십이만이천' → 3320000+2000. 지원 범위는 조 단위까지."""
    s = re.sub(r"[^가-힣]", "", s)
    s = re.sub(r"^(금|일금)", "", s)
    s = re.sub(r"(원정?|정)$", "", s)
    if not s:
        return None
    total = 0
    big_chunk = 0
    small = 0
    digit = 0
    seen = False
    for ch in s:
        if ch in _HANGUL_DIGIT:
            digit = _HANGUL_DIGIT[ch]
            seen = True
        elif ch in _HANGUL_UNIT:
            small += (digit or 1) * _HANGUL_UNIT[ch]
            digit = 0
            seen = True
        elif ch in _HANGUL_BIG:
            big_chunk = (big_chunk + small + digit) or 1
            total += big_chunk * _HANGUL_BIG[ch]
            big_chunk = small = digit = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + small + digit


_DATE_PATTERNS = (
    re.compile(r"(\d{4})\s*[.\-년/]\s*(\d{1,2})\s*[.\-월/]\s*(\d{1,2})"),
    re.compile(r"(\d{4})(\d{2})(\d{2})(?!\d)"),
    re.compile(r"(\d{2})\s*[.\-]\s*(\d{1,2})\s*[.\-]\s*(\d{1,2})"),
)


def parse_date(value) -> Optional[date]:
    """'2026-03-05' '2026. 3. 5.' '2026년 3월 5일' '26.3.5' '20260305' → date"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = unicodedata.normalize("NFKC", str(value)).strip()
    if not s:
        return None
    for i, pat in enumerate(_DATE_PATTERNS):
        m = pat.search(s)
        if not m:
            continue
        y, mo, d = (int(g) for g in m.groups())
        if i == 2:                      # 두 자리 연도
            y += 2000
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def normalize_biz_no(value) -> Optional[str]:
    """숫자만 뽑아 '000-00-00000' 형태로."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) != 10:
        return None
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"


def valid_biz_no(value) -> bool:
    """사업자등록번호 체크섬 검증.

    가중치 1,3,7,1,3,7,1,3,5 를 앞 9자리에 곱해 더하고
    9번째 자리 × 5 의 십의 자리를 더한 뒤, 10의 보수의 일의 자리가 검증번호.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 10:
        return False
    nums = [int(c) for c in digits]
    weights = (1, 3, 7, 1, 3, 7, 1, 3, 5)
    total = sum(n * w for n, w in zip(nums[:9], weights))
    total += (nums[8] * 5) // 10
    return (10 - total % 10) % 10 == nums[9]


_CORP_PREFIX = re.compile(r"^\s*(\(주\)|㈜|주식회사|\(유\)|유한회사|\(재\)|재단법인|\(사\)|사단법인)\s*")
_CORP_SUFFIX = re.compile(r"\s*(\(주\)|㈜|주식회사|\(유\)|유한회사)\s*$")


def normalize_vendor_name(value) -> Optional[str]:
    """'(주) 마음교구 ' / '마음교구(주)' / '㈜마음교구' → '마음교구' (매칭 키용)."""
    if value is None:
        return None
    s = unicodedata.normalize("NFKC", str(value)).strip()
    s = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", s)   # 뒤쪽 괄호(사업자번호 등) 제거
    s = _CORP_PREFIX.sub("", s)
    s = _CORP_SUFFIX.sub("", s)
    s = re.sub(r"\s+", "", s)
    return s or None


def format_money(n: Optional[int]) -> str:
    return "—" if n is None else f"{n:,}"


_K_DIGITS = "영일이삼사오육칠팔구"
_K_SMALL = ("", "십", "백", "천")
_K_BIG = ("", "만", "억", "조")


def korean_amount(n: int) -> str:
    """3322000 → '삼백삼십이만이천' (기안문 '금 ...원정' 표기 검증용)."""
    if n == 0:
        return "영"
    chunks = []
    while n > 0:
        chunks.append(n % 10000)
        n //= 10000
    out = []
    for idx in range(len(chunks) - 1, -1, -1):
        c = chunks[idx]
        if c == 0:
            continue
        part = ""
        for pos, unit in ((3, "천"), (2, "백"), (1, "십"), (0, "")):
            d = (c // (10 ** pos)) % 10
            if d == 0:
                continue
            part += ("" if (d == 1 and unit) else _K_DIGITS[d]) + unit
        out.append(part + _K_BIG[idx])
    return "".join(out)
