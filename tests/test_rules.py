"""룰별 단위 테스트 — 각 룰이 잡아야 할 것을 잡고, 아닌 건 통과시키는지."""
from datetime import date

import pytest

from jeongsan import (BudgetLine, Config, Draft, Item, PriorSpend, Project,
                      Quote, TaxInvoice, Vendor, validate)
from jeongsan.models import ERROR, OK, WARNING


def codes(result, severity=None):
    return {f.code for f in result.findings
            if severity is None or f.severity == severity}


def find(result, code):
    return [f for f in result.findings if f.code == code]


def sev(result, code):
    fs = find(result, code)
    assert fs, f"{code} 판정이 없습니다"
    return fs[0].severity


# ---------------------------------------------------------------- 픽스처

@pytest.fixture
def project():
    return Project(
        name="테스트 사업", grant=10_000_000,
        start=date(2026, 3, 1), end=date(2026, 11, 30),
        lines=[BudgetLine("자료 구입", 5_000_000)],
        prior=[PriorSpend(date(2026, 3, 2), "(주)마음교구", "214-88-01232",
                          1_000_000, "자료 구입")],
        purpose="상담 활성화",
    )


def make_quote(items=None, supply=1_000_000, vat=100_000, total=1_100_000,
               quote_date=date(2026, 3, 5), valid_days=30,
               biz_no="214-88-01232", **kw):
    return Quote(
        vendor=Vendor(name="(주)마음교구", biz_no=biz_no),
        quote_date=quote_date, valid_days=valid_days,
        items=items if items is not None else [
            Item(name="워크북", qty=100, unit_price=10_000, amount=1_000_000)],
        supply=supply, vat=vat, total=total, **kw)


def make_draft(amount=1_100_000, estimated=1_000_000, quote_count=1,
               draft_date=date(2026, 3, 6), due_date=date(2026, 3, 25),
               budget_code="목적사업비 / 일반운영비", method="수의계약(1인 견적)",
               biz_no="214-88-01232", **kw):
    return Draft(amount=amount, estimated=estimated, quote_count=quote_count,
                 draft_date=draft_date, due_date=due_date,
                 budget_code=budget_code, method=method,
                 vendor=Vendor(name="(주)마음교구", biz_no=biz_no), **kw)


# ------------------------------------------------------------ 정상 기준선

def test_clean_case_has_no_errors(project):
    r = validate(project, make_draft(), make_quote(), line="자료 구입")
    assert r.ok, [str(f) for f in r.errors]
    assert r.this_amount == 1_100_000
    assert r.total_spent == 2_100_000
    assert r.remaining == 7_900_000


# --------------------------------------------------------------- R01~R05

def test_r01_catches_line_item_arithmetic(project):
    q = make_quote(items=[Item("패널", qty=24, unit_price=22_000, amount=550_000)],
                   supply=550_000, vat=55_000, total=605_000)
    r = validate(project, quote=q, line="자료 구입")
    assert sev(r, "R01") == ERROR
    assert "528,000" in find(r, "R01")[0].detail


def test_r01_passes_when_correct(project):
    r = validate(project, quote=make_quote(), line="자료 구입")
    assert sev(r, "R01") == OK


def test_r01_passes_with_fractional_qty(project):
    """2.5시간 × 20,000 = 50,000. 수량을 int로 자르면 허위 오류가 난다."""
    q = make_quote(items=[Item("강사료", qty=2.5, unit_price=20_000, amount=50_000)],
                   supply=50_000, vat=5_000, total=55_000)
    r = validate(project, quote=q, line="자료 구입")
    assert sev(r, "R01") == OK


def test_r01_detail_shows_qty_unrounded(project):
    """오류 메시지의 수량이 반올림되면 앞뒤가 안 맞는 문장이 된다."""
    q = make_quote(items=[Item("강사료", qty=2.5, unit_price=20_000, amount=60_000)],
                   supply=60_000, vat=6_000, total=66_000)
    r = validate(project, quote=q, line="자료 구입")
    assert sev(r, "R01") == ERROR
    assert "2.5 × 20,000" in find(r, "R01")[0].detail


def test_r02_items_sum_mismatch(project):
    q = make_quote(items=[Item("워크북", qty=10, unit_price=10_000, amount=100_000)],
                   supply=1_000_000)
    r = validate(project, quote=q, line="자료 구입")
    assert sev(r, "R02") == ERROR


def test_r03_vat(project):
    r = validate(project, quote=make_quote(vat=90_000, total=1_090_000),
                 line="자료 구입")
    assert sev(r, "R03") == ERROR


def test_r03_skipped_when_tax_free(project):
    r = validate(project, quote=make_quote(vat=0, total=1_000_000, tax_free=True),
                 line="자료 구입")
    assert sev(r, "R03") != ERROR


def test_r04_total_mismatch(project):
    r = validate(project, quote=make_quote(total=1_200_000), line="자료 구입")
    assert sev(r, "R04") == ERROR


def test_r05_three_way_mismatch(project):
    tax = TaxInvoice(total=1_200_000, supply=1_090_909, vat=109_091,
                     vendor=Vendor(biz_no="214-88-01232"))
    r = validate(project, make_draft(), make_quote(), tax, line="자료 구입")
    assert sev(r, "R05") == ERROR
    assert "차액" in find(r, "R05")[0].detail


def test_r05_passes_when_all_equal(project):
    tax = TaxInvoice(total=1_100_000, supply=1_000_000, vat=100_000,
                     vendor=Vendor(biz_no="214-88-01232"),
                     write_date=date(2026, 3, 20))
    r = validate(project, make_draft(), make_quote(), tax, line="자료 구입")
    assert sev(r, "R05") == OK


# --------------------------------------------------------------- R06~R08

def test_r06_grant_exceeded(project):
    r = validate(project, make_draft(amount=9_500_000, estimated=8_636_364),
                 line="자료 구입")
    assert sev(r, "R06") == ERROR


def test_r07_line_budget_exceeded(project):
    r = validate(project, make_draft(amount=4_500_000, estimated=4_090_909),
                 line="자료 구입")
    assert sev(r, "R07") == ERROR
    assert sev(r, "R06") == OK          # 사업 전체로는 아직 여유


def test_r07_unknown_line_warns(project):
    r = validate(project, make_draft(), line="없는 항목")
    assert sev(r, "R07") == WARNING


def test_r07_warns_when_line_omitted():
    """항목이 여럿이면 자동 선택이 안 된다. 조용히 빠지지 말고 알려야 한다."""
    p = Project(name="다항목 사업", grant=10_000_000,
                start=date(2026, 3, 1), end=date(2026, 11, 30),
                lines=[BudgetLine("자료 구입", 5_000_000),
                       BudgetLine("소모품", 2_000_000)])
    r = validate(p, make_draft())
    assert sev(r, "R07") == WARNING


def test_r07_autoselects_single_line(project):
    """항목이 하나뿐이면 자동 선택되므로 경고가 아니라 판정이 나와야 한다."""
    r = validate(project, make_draft())
    assert sev(r, "R07") == OK


def test_r07_silent_when_project_has_no_lines():
    """세부항목 예산 자체가 없는 사업이면 경고할 게 없다."""
    p = Project(name="단일 항목 사업", grant=10_000_000,
                start=date(2026, 3, 1), end=date(2026, 11, 30))
    r = validate(p, make_draft())
    assert find(r, "R07") == []


def test_r08_asset_code_warning(project):
    q = make_quote(items=[Item("빔프로젝터", qty=1, unit_price=1_250_000,
                               amount=1_250_000)],
                   supply=1_250_000, vat=125_000, total=1_375_000)
    r = validate(project, make_draft(amount=1_375_000, estimated=1_250_000), q,
                 line="자료 구입")
    assert sev(r, "R08") == WARNING


def test_r08_ok_for_cheap_items(project):
    r = validate(project, make_draft(), make_quote(), line="자료 구입")
    assert sev(r, "R08") == OK


# --------------------------------------------------------------- R09~R12

@pytest.mark.parametrize("estimated,quotes,expected", [
    (19_000_000, 1, OK),        # 2천만원 이하 → 1인 견적 가능
    (24_000_000, 1, ERROR),     # 2천만원 초과 → 2인 이상 필요
    (24_000_000, 2, OK),
    (150_000_000, 2, ERROR),    # 1억원 초과 → 입찰
])
def test_r09_contract_method(project, estimated, quotes, expected):
    p = Project(name="x", grant=500_000_000, start=date(2026, 3, 1),
                end=date(2026, 11, 30), lines=[BudgetLine("자료 구입", 500_000_000)])
    d = make_draft(amount=int(estimated * 1.1), estimated=estimated,
                   quote_count=quotes)
    r = validate(p, d, line="자료 구입")
    assert sev(r, "R09") == expected


def test_r09_bid_method_accepted(project):
    p = Project(name="x", grant=500_000_000, lines=[BudgetLine("자료 구입", 500_000_000)])
    d = make_draft(amount=165_000_000, estimated=150_000_000, quote_count=None,
                   method="입찰(일반)")
    r = validate(p, d, line="자료 구입")
    assert sev(r, "R09") == OK


def test_r09_special_entity_raises_threshold(project):
    p = Project(name="x", grant=100_000_000, lines=[BudgetLine("자료 구입", 100_000_000)])
    d = make_draft(amount=44_000_000, estimated=40_000_000, quote_count=1)
    d.vendor.special_entity = True
    r = validate(p, d, line="자료 구입")
    assert sev(r, "R09") == OK          # 여성기업 특례 5천만원 이하 1인 견적


def test_r10_special_entity_hint(project):
    p = Project(name="x", grant=100_000_000, lines=[BudgetLine("자료 구입", 100_000_000)])
    d = make_draft(amount=33_000_000, estimated=30_000_000, quote_count=2)
    r = validate(p, d, line="자료 구입")
    assert find(r, "R10"), "2천만~5천만 구간에서는 특례 안내가 나와야 함"


def test_r11_lower_limit_value(project):
    r = validate(project, make_draft(), make_quote(), line="자료 구입")
    f = find(r, "R11")[0]
    assert f.evidence["floor"] == 900_000        # 1,000,000 × 90%


def test_r12_expired_quote(project):
    q = make_quote(quote_date=date(2026, 4, 2), valid_days=30)
    d = make_draft(draft_date=date(2026, 6, 10), due_date=date(2026, 6, 30))
    r = validate(project, d, q, line="자료 구입")
    assert sev(r, "R12") == ERROR
    assert "경과 39일" in find(r, "R12")[0].detail


def test_r12_quote_after_draft(project):
    q = make_quote(quote_date=date(2026, 3, 10))
    d = make_draft(draft_date=date(2026, 3, 6))
    r = validate(project, d, q, line="자료 구입")
    assert sev(r, "R12") == ERROR


# --------------------------------------------------------------- R13~R14

def test_r13_dates_outside_period(project):
    d = make_draft(draft_date=date(2026, 12, 14), due_date=date(2026, 12, 28))
    q = make_quote(quote_date=date(2026, 12, 12), valid_days=15)
    r = validate(project, d, q, line="자료 구입")
    assert sev(r, "R13") == ERROR
    assert "기안일" in find(r, "R13")[0].detail


def test_r14_bad_checksum(project):
    r = validate(project, quote=make_quote(biz_no="214-88-01233"), line="자료 구입")
    assert sev(r, "R14") == ERROR


def test_r14_mismatch_between_documents(project):
    d = make_draft(biz_no="305-82-00459")
    q = make_quote(biz_no="214-88-01232")
    r = validate(project, d, q, line="자료 구입")
    msgs = [f.title for f in find(r, "R14")]
    assert any("불일치" in m for m in msgs)


def test_r14_missing_warns(project):
    q = make_quote(biz_no=None)
    q.vendor.biz_no = None
    r = validate(project, quote=q, line="자료 구입")
    assert sev(r, "R14") == WARNING


# --------------------------------------------------------------- R15~R16

def test_r15_split_contract_suspicion():
    p = Project(name="x", grant=100_000_000,
                start=date(2026, 3, 1), end=date(2026, 11, 30),
                lines=[BudgetLine("자료 구입", 100_000_000)],
                prior=[PriorSpend(date(2026, 3, 2), "(주)마음교구",
                                  "214-88-01232", 18_000_000, "자료 구입")])
    d = make_draft(amount=16_500_000, estimated=15_000_000)
    q = make_quote(supply=15_000_000, vat=1_500_000, total=16_500_000,
                   items=[Item("교구", qty=1, unit_price=15_000_000,
                               amount=15_000_000)])
    r = validate(p, d, q, line="자료 구입")
    assert sev(r, "R15") == WARNING
    assert "합산" in find(r, "R15")[0].detail


def test_r15_no_flag_when_bracket_unchanged(project):
    r = validate(project, make_draft(), make_quote(), line="자료 구입")
    f = find(r, "R15")
    assert f and f[0].severity != WARNING


def test_r15_ignores_distant_dates():
    p = Project(name="x", grant=100_000_000,
                lines=[BudgetLine("자료 구입", 100_000_000)],
                prior=[PriorSpend(date(2026, 1, 2), "(주)마음교구",
                                  "214-88-01232", 18_000_000, "자료 구입")])
    r = validate(p, make_draft(), make_quote(), line="자료 구입")
    assert not find(r, "R15")


def test_r16_reports_items(project):
    r = validate(project, quote=make_quote(), line="자료 구입")
    assert find(r, "R16")[0].evidence["items"] == ["워크북"]


# ----------------------------------------------------------------- 기타

def test_config_override_changes_verdict(project):
    """지침 개정 시 설정만 바꿔서 판정이 달라져야 한다."""
    p = Project(name="x", grant=500_000_000,
                lines=[BudgetLine("자료 구입", 500_000_000)])
    d = make_draft(amount=33_000_000, estimated=30_000_000, quote_count=1)
    assert sev(validate(p, d, line="자료 구입"), "R09") == ERROR
    relaxed = Config(single_quote_max=50_000_000)
    assert sev(validate(p, d, line="자료 구입", config=relaxed), "R09") == OK


def test_only_and_skip(project):
    r = validate(project, make_draft(), make_quote(), line="자료 구입",
                 only=["R01", "R03"])
    assert codes(r) == {"R01", "R03"}
    r2 = validate(project, make_draft(), make_quote(), line="자료 구입",
                  skip=["R16"])
    assert "R16" not in codes(r2)


def test_requires_at_least_one_document(project):
    with pytest.raises(ValueError):
        validate(project)


def test_line_auto_selected_when_single(project):
    r = validate(project, make_draft(), make_quote())
    assert r.line_name == "자료 구입"


def test_tolerance_absorbs_rounding(project):
    q = make_quote(supply=1_000_050, vat=100_000, total=1_100_050)
    r = validate(project, quote=q, line="자료 구입")
    assert sev(r, "R03") == ERROR       # 5원 차이는 오차 범위 밖(허용 1원)
    loose = Config(tolerance=10)
    r2 = validate(project, quote=q, line="자료 구입", config=loose)
    assert sev(r2, "R03") == OK


def test_vat_rounding_is_not_bankers(project):
    """100000.5 → 은행가 반올림이면 100000. 회계에서는 그 동작을 쓰지 않는다."""
    q = make_quote(supply=1_000_005, vat=100_000, total=1_100_005)
    # 기본값 floor(절사) → 100,000 이므로 통과
    assert sev(validate(project, quote=q, line="자료 구입"), "R03") == OK
    # half_up 이면 100,001 을 기대하므로 불일치
    up = Config(vat_rounding="half_up", tolerance=0)
    assert sev(validate(project, quote=q, line="자료 구입", config=up), "R03") == ERROR
