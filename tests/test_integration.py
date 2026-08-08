"""실제 문서 텍스트 → 추출 → 검증 → 산출물 전 구간 통합 테스트."""
import json
import os
from datetime import date

import pytest

from jeongsan import (draft_from_text, html_report, load_project,
                      quote_from_dict, quote_from_text, refund_draft,
                      tax_from_text, text_report, validate, write_xlsx)
from jeongsan.cli import main
from jeongsan.models import ERROR, OK

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
LINE = "검사·상담 자료 구입"


def read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture
def project():
    return load_project(os.path.join(FIX, "사업.json"))


# ------------------------------------------------------------------ 추출

def test_quote_extraction():
    q = quote_from_text(read("견적서_정상.txt"))
    assert q.vendor.name == "(주)마음교구"
    assert q.vendor.biz_no == "214-88-01232"
    assert q.vendor.ceo == "김정민"
    assert q.quote_date == date(2026, 3, 5)
    assert q.valid_days == 30
    assert q.supply == 3_020_000
    assert q.vat == 302_000
    assert q.total == 3_322_000
    assert len(q.items) == 3
    assert q.items[0].name == "정서행동 심리검사 도구"
    assert q.items[0].unit == "세트"
    assert q.items[0].spec == "중등용 세트"
    assert q.items[0].qty == 30
    assert q.items[0].unit_price == 48_000
    assert q.items_sum == 3_020_000


def test_draft_extraction():
    d = draft_from_text(read("기안_정상.txt"))
    assert d.doc_no == "OO중학교-2026-0417"
    assert d.project == "2026 학생 정서행동 지원사업"
    assert d.draft_date == date(2026, 3, 6)
    assert d.amount == 3_322_000
    assert d.estimated == 3_020_000
    assert d.quote_count == 1
    assert d.due_date == date(2026, 3, 25)
    assert d.vendor.name == "(주)마음교구"
    assert d.vendor.biz_no == "214-88-01232"
    assert "일반운영비" in d.budget_code


def test_tax_extraction():
    t = tax_from_text(read("세금계산서_정상.txt"))
    assert t.write_date == date(2026, 3, 18)
    assert t.supply == 3_020_000
    assert t.vat == 302_000
    assert t.total == 3_322_000
    assert t.vendor.biz_no == "214-88-01232"


def test_extraction_never_invents_values():
    """빈 문서에서 값을 지어내지 않는다."""
    q = quote_from_text("견 적 서\n\n수신: OO중학교 귀중\n")
    assert q.supply is None and q.vat is None and q.total is None
    assert q.vendor.biz_no is None
    assert q.items == []
    assert q.confidence["supply"] == 0.0


def test_empty_input_returns_none():
    assert quote_from_text("") is None
    assert draft_from_text("   ") is None
    assert tax_from_text(None) is None


# ----------------------------------------------------- 시나리오 ① 정상 건

def test_scenario_clean(project):
    r = validate(project,
                 draft_from_text(read("기안_정상.txt")),
                 quote_from_text(read("견적서_정상.txt")),
                 tax_from_text(read("세금계산서_정상.txt")),
                 line=LINE)
    assert r.ok, [str(f) for f in r.errors]
    assert r.this_amount == 3_322_000
    assert r.prior_total == 4_170_000
    assert r.total_spent == 7_492_000
    assert r.remaining == 4_508_000
    assert r.line_spent == 5_302_000
    assert r.line_allocated == 6_000_000


# -------------------------------------------------- 시나리오 ② 오류 다발

def test_scenario_errors(project):
    r = validate(project,
                 draft_from_text(read("기안_오류.txt")),
                 quote_from_text(read("견적서_오류.txt")),
                 line=LINE)
    assert not r.ok
    bad = {f.code for f in r.errors}
    # 품목 검산 / 부가세 / 3자 대조 / 계약방법 / 견적 유효기간 / 사업자번호
    assert {"R01", "R03", "R05", "R09", "R12", "R14"} <= bad
    # 품목 합계 자체는 공급가액과 맞으므로 R02는 통과해야 한다
    assert [f.severity for f in r.findings if f.code == "R02"] == [OK]
    # 빔프로젝터 단가 → 자산취득비 확인 경고
    assert any(f.code == "R08" for f in r.warnings)


def test_scenario_errors_details(project):
    r = validate(project,
                 draft_from_text(read("기안_오류.txt")),
                 quote_from_text(read("견적서_오류.txt")),
                 line=LINE)
    d = {f.code: f for f in r.errors}
    assert "528,000" in d["R01"].detail
    assert "324,000" in d["R03"].detail
    assert d["R05"].evidence["values"] == {"기안": 2_640_000, "견적": 3_550_000}
    assert d["R09"].evidence["required"] == 2
    assert d["R14"].evidence["biz_no"] == "214-88-01233"


# ----------------------------------------------- 시나리오 ③ 예산·기간 초과

def test_scenario_overrun(project):
    r = validate(project,
                 draft_from_text(read("기안_초과.txt")),
                 quote_from_text(read("견적서_초과.txt")),
                 line=LINE)
    assert not r.ok
    bad = {f.code for f in r.errors}
    assert "R07" in bad            # 세부항목 배정액 초과
    assert "R13" in bad            # 집행기간 밖
    assert "R06" not in bad        # 사업 전체로는 아직 교부액 이내
    assert [f.severity for f in r.findings if f.code == "R01"] == [OK]


# ---------------------------------------------------------- LLM 경로 동치

def test_llm_dict_path_matches_text_path(project):
    """정규식 경로와 LLM(JSON) 경로가 같은 검증 결과를 내야 한다."""
    q_text = quote_from_text(read("견적서_정상.txt"))
    q_llm = quote_from_dict({
        "vendor": {"name": "(주)마음교구", "biz_no": "214-88-01232", "ceo": "김정민"},
        "quote_date": "2026-03-05", "valid_days": 30,
        "items": [
            {"name": "정서행동 심리검사 도구", "spec": "중등용 세트", "unit": "세트",
             "qty": 30, "unit_price": 48000, "amount": 1440000},
            {"name": "상담 워크북", "spec": "A4 120p", "unit": "권",
             "qty": 120, "unit_price": 7500, "amount": 900000},
            {"name": "모래놀이 치료도구", "spec": "기본형", "unit": "SET",
             "qty": 2, "unit_price": 340000, "amount": 680000},
        ],
        "supply": 3020000, "vat": 302000, "total": 3322000,
    })
    d = draft_from_text(read("기안_정상.txt"))
    a = validate(project, d, q_text, line=LINE)
    b = validate(project, d, q_llm, line=LINE)
    assert [(f.code, f.severity) for f in a.sorted_findings] == \
           [(f.code, f.severity) for f in b.sorted_findings]


# ------------------------------------------------------------------ 산출물

def test_reports_render(project, tmp_path):
    d = draft_from_text(read("기안_정상.txt"))
    q = quote_from_text(read("견적서_정상.txt"))
    r = validate(project, d, q, line=LINE)

    txt = text_report(r, project)
    assert "2026 학생 정서행동 지원사업" in txt
    assert "확정 가능" in txt

    doc = html_report(r, project, d, q)
    assert doc.startswith("<!DOCTYPE html>")
    assert "집행내역서" in doc and "4,508,000" in doc

    pytest.importorskip("openpyxl", reason="xlsx는 선택 의존성입니다")
    out = tmp_path / "집행내역서.xlsx"
    write_xlsx(str(out), project, r, d, q)
    assert out.exists() and out.stat().st_size > 3000


def test_refund_draft_text(project):
    r = validate(project, draft_from_text(read("기안_정상.txt")),
                 quote_from_text(read("견적서_정상.txt")), line=LINE)
    rd = refund_draft(project, r)
    assert "4,508,000" in rd
    assert "사백오십만팔천" in rd


def test_refund_draft_none_when_fully_spent():
    from jeongsan import BudgetLine, Project
    p = Project(name="x", grant=1_000_000, lines=[BudgetLine("a", 1_000_000)])
    from jeongsan import Draft
    r = validate(p, Draft(amount=1_000_000, estimated=1_000_000), line="a")
    assert refund_draft(p, r) is None


# --------------------------------------------------------------------- CLI

def test_cli_check_clean(capsys, project):
    rc = main(["check",
               "--project", os.path.join(FIX, "사업.json"),
               "--draft", os.path.join(FIX, "기안_정상.txt"),
               "--quote", os.path.join(FIX, "견적서_정상.txt"),
               "--line", LINE, "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["remaining"] == 4_508_000
    assert payload["summary"]["error"] == 0


def test_cli_check_returns_1_on_errors(capsys):
    rc = main(["check",
               "--project", os.path.join(FIX, "사업.json"),
               "--draft", os.path.join(FIX, "기안_오류.txt"),
               "--quote", os.path.join(FIX, "견적서_오류.txt"),
               "--line", LINE, "--json"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["error"] >= 6


def test_cli_writes_artifacts(tmp_path, capsys):
    pytest.importorskip("openpyxl", reason="xlsx는 선택 의존성입니다")
    html_out, xlsx_out = tmp_path / "r.html", tmp_path / "s.xlsx"
    main(["check", "--project", os.path.join(FIX, "사업.json"),
          "--quote", os.path.join(FIX, "견적서_정상.txt"),
          "--line", LINE, "--html", str(html_out), "--xlsx", str(xlsx_out)])
    assert html_out.exists() and xlsx_out.exists()


def test_cli_bizno(capsys):
    assert main(["bizno", "214-88-01232"]) == 0
    assert "유효" in capsys.readouterr().out
    assert main(["bizno", "214-88-01233"]) == 1


def test_cli_rules_and_schema(capsys):
    main(["rules", "--json"])
    rules = json.loads(capsys.readouterr().out)
    assert len(rules) == 16
    assert rules[0]["code"] == "R01"

    main(["schema", "quote"])
    schema = json.loads(capsys.readouterr().out)
    assert "items" in schema["properties"]
