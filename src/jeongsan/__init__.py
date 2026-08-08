"""jeongsan — 학교회계 목적사업비 정산 검증 엔진.

    from jeongsan import validate, load_project, quote_from_text, draft_from_text

    project = load_project("사업.json")
    result  = validate(project,
                       draft_from_text(open("기안.txt").read()),
                       quote_from_text(open("견적서.txt").read()))
    print(result.ok, result.summary())
    for f in result.errors:
        print(f)

설계 원칙: **LLM은 추출만, 계산·검증은 코드.**
이 패키지에는 네트워크 호출이 없다. 룰은 전부 결정론적이라 테스트 가능하다.
"""
from .config import Config, load_config
from .engine import validate
from .extract import (DRAFT_SCHEMA, EXTRACTION_PROMPT, QUOTE_SCHEMA, TAX_SCHEMA,
                      draft_from_dict, draft_from_text, quote_from_dict,
                      quote_from_text, tax_from_dict, tax_from_text)
from .loader import load_project, project_from_dict
from .models import (BudgetLine, Draft, Finding, Item, PriorSpend, Project,
                     Quote, Result, TaxInvoice, Vendor)
from .normalize import (korean_amount, normalize_biz_no, parse_date,
                        parse_money, valid_biz_no)
from .report import html_report, refund_draft, text_report, write_xlsx
from .rules import RULES, Context

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # 진입점
    "validate", "Context",
    # 모델
    "Project", "BudgetLine", "PriorSpend", "Draft", "Quote", "TaxInvoice",
    "Item", "Vendor", "Finding", "Result",
    # 설정
    "Config", "load_config",
    # 로더·추출
    "load_project", "project_from_dict",
    "draft_from_text", "quote_from_text", "tax_from_text",
    "draft_from_dict", "quote_from_dict", "tax_from_dict",
    "QUOTE_SCHEMA", "DRAFT_SCHEMA", "TAX_SCHEMA", "EXTRACTION_PROMPT",
    # 정규화
    "parse_money", "parse_date", "normalize_biz_no", "valid_biz_no",
    "korean_amount",
    # 리포트
    "text_report", "html_report", "write_xlsx", "refund_draft",
    # 룰
    "RULES",
]
