from datetime import date

import pytest

from jeongsan.normalize import (korean_amount, normalize_biz_no,
                                normalize_vendor_name, parse_date, parse_money,
                                valid_biz_no)


@pytest.mark.parametrize("raw,expected", [
    ("1,234,000원", 1234000),
    ("￦1,234,000", 1234000),
    ("금 3,322,000원", 3322000),
    ("3322000", 3322000),
    (3322000, 3322000),
    ("  ", None),
    (None, None),
    ("금삼백삼십이만이천원", 3322000),
    ("일억이천만", 120000000),
    ("오천", 5000),
])
def test_parse_money(raw, expected):
    assert parse_money(raw) == expected


@pytest.mark.parametrize("raw", ["2026-03-05", "2026. 3. 5.", "2026년 3월 5일",
                                 "26.3.5", "20260305", "2026/03/05"])
def test_parse_date_variants(raw):
    assert parse_date(raw) == date(2026, 3, 5)


def test_parse_date_invalid():
    assert parse_date("2026-13-45") is None
    assert parse_date("") is None
    assert parse_date(None) is None
    assert parse_date(date(2026, 1, 1)) == date(2026, 1, 1)


@pytest.mark.parametrize("no", ["220-81-62517", "124-81-00998",
                                "214-88-01232", "305-82-00459", "617-81-07775"])
def test_biz_no_valid(no):
    assert valid_biz_no(no) is True


@pytest.mark.parametrize("no", ["214-88-01233", "123-45-67890", "12345", "",
                                None, "abc-de-fghij"])
def test_biz_no_invalid(no):
    assert valid_biz_no(no) is False


def test_biz_no_accepts_unhyphenated():
    assert valid_biz_no("2148801232") is True
    assert normalize_biz_no("2148801232") == "214-88-01232"
    assert normalize_biz_no("214 88 01232") == "214-88-01232"
    assert normalize_biz_no("21488") is None


@pytest.mark.parametrize("raw", ["(주)마음교구", "㈜마음교구", "주식회사 마음교구",
                                 "마음교구(주)", " (주) 마음교구 ",
                                 "(주)마음교구 (214-88-01232)"])
def test_vendor_name_normalization(raw):
    assert normalize_vendor_name(raw) == "마음교구"


@pytest.mark.parametrize("n,expected", [
    (3322000, "삼백삼십이만이천"),
    (10000, "일만"),
    (0, "영"),
    (120000000, "일억이천만"),
    (5000, "오천"),
])
def test_korean_amount(n, expected):
    assert korean_amount(n) == expected


def test_korean_amount_roundtrip():
    for n in (1000, 12345, 3322000, 120000000, 987654321):
        assert parse_money(korean_amount(n)) == n
