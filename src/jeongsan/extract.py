"""문서 → 모델 추출.

두 갈래를 제공한다.

1. :func:`quote_from_text` 등 — **정규식 기반**. LLM 없이 동작하며
   깨끗한 텍스트(HWPX/텍스트 PDF/엑셀 붙여넣기)에서 잘 맞는다.
2. :func:`quote_from_dict` 등 — LLM이 :data:`QUOTE_SCHEMA` 에 맞춰 뱉은
   JSON을 그대로 모델로 바꾼다. 흐린 스캔본은 이쪽을 쓴다.

두 경로 모두 결과 타입이 같으므로 뒤쪽 룰 엔진은 출처를 신경 쓰지 않는다.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from .models import Draft, Item, Quote, TaxInvoice, Vendor
from .normalize import (normalize_biz_no, parse_date, parse_money,
                        normalize_vendor_name)

__all__ = [
    "quote_from_text", "draft_from_text", "tax_from_text",
    "quote_from_dict", "draft_from_dict", "tax_from_dict",
    "QUOTE_SCHEMA", "DRAFT_SCHEMA", "TAX_SCHEMA", "EXTRACTION_PROMPT",
]

CONF_PRIMARY = 0.97      # 첫 번째(가장 명시적인) 패턴으로 잡힘
CONF_FALLBACK = 0.83     # 보조 패턴으로 잡힘
CONF_NONE = 0.0


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _grab(text: str, patterns) -> Tuple[Optional[str], float]:
    for idx, pat in enumerate(patterns):
        m = re.search(pat, text, re.MULTILINE)
        if m:
            val = (m.group(1) or "").strip()
            if val:
                return val, (CONF_PRIMARY if idx == 0 else CONF_FALLBACK)
    return None, CONF_NONE


_STOP_ROW = re.compile(r"(공급\s*가액|부가\s*(가치)?세|합\s*계|소\s*계|총\s*액|총\s*계|이하\s*여백)")
_NUMERIC = re.compile(r"^[\d,]+(\.\d+)?$")


def _parse_item_rows(text: str) -> List[Item]:
    """표 형태 텍스트에서 품목 행을 뽑는다.

    탭 구분 우선, 없으면 2칸 이상 공백 또는 '|' 구분.
    오른쪽 끝에서 숫자 칼럼 3개(수량·단가·금액)를 찾는 방식이라
    칼럼 개수가 문서마다 달라도 견딘다.
    """
    items: List[Item] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or _STOP_ROW.search(line):
            continue
        cols = [c.strip() for c in line.split("\t") if c.strip()]
        if len(cols) < 4:
            cols = [c.strip() for c in re.split(r"\s{2,}|\s*\|\s*", line) if c.strip()]
        if len(cols) < 4:
            continue

        tail: List[Tuple[int, Optional[int]]] = []
        for i in range(len(cols) - 1, -1, -1):
            if len(tail) >= 3:
                break
            if _NUMERIC.match(cols[i].replace("원", "").strip()):
                tail.insert(0, (i, parse_money(cols[i])))
            else:
                break
        if len(tail) < 3:
            continue
        first_num = tail[0][0]
        if first_num == 0:                 # 품명 칼럼이 없으면 표 행이 아니다
            continue

        mid = cols[1:first_num]
        unit = ""
        if len(mid) >= 2 and len(mid[-1]) <= 4:
            unit = mid.pop()
        _, unit_price, amount = (t[1] or 0 for t in tail)
        # 수량은 소수가 될 수 있다 (2.5시간, 1.5m). parse_money 는 int 라 못 쓴다.
        qty = float(cols[tail[0][0]].replace(",", "").rstrip("원") or 0)
        items.append(Item(name=cols[0], spec=" ".join(mid), unit=unit,
                          qty=qty, unit_price=unit_price, amount=amount))
    return items


# ---------------------------------------------------------------- 견적서

def quote_from_text(text: str, source: Optional[str] = None) -> Optional[Quote]:
    if not (text or "").strip():
        return None
    t = _norm(text)
    conf: Dict[str, float] = {}

    name, c = _grab(t, [r"(?:상\s*호|업체명|공급자\s*명|회사명)\s*[:：]?\s*([^\n]+)",
                        r"((?:\(주\)|㈜|주식회사)\s*[^\n,]{1,20})"])
    conf["vendor"] = c
    biz, c = _grab(t, [r"(?:사업자\s*등록\s*번호|사업자번호)\s*[:：]?\s*([\d\-\s]{10,16})",
                       r"(\d{3}-\d{2}-\d{5})"])
    biz = normalize_biz_no(biz)
    conf["biz_no"] = c if biz else CONF_NONE
    ceo, c = _grab(t, [r"(?:대\s*표\s*자|대표이사)\s*[:：]?\s*([^\n]{1,20})"])
    conf["ceo"] = c

    qd_raw, c = _grab(t, [r"(?:견적\s*일자?|작성일자?)\s*[:：]?\s*([^\n]+)",
                          r"^\s*(\d{4}\s*[.년\-]\s*\d{1,2}\s*[.월\-]\s*\d{1,2})"])
    quote_date = parse_date(qd_raw)
    conf["quote_date"] = c if quote_date else CONF_NONE

    m = re.search(r"유효\s*기간\s*[:：]?\s*(?:견적일(?:로|)부터\s*)?(\d+)\s*일", t)
    valid_days = int(m.group(1)) if m else None
    conf["valid_days"] = 0.95 if valid_days else CONF_NONE

    supply = parse_money(_search(t, r"공급\s*가액\s*[:：]?\s*([\d,]+)"))
    vat = parse_money(_search(t, r"부가\s*(?:가치)?세(?:액)?\s*[:：]?\s*([\d,]+)"))
    total = parse_money(_search(t, r"(?:합\s*계\s*금?액?|총\s*액|총\s*계)\s*[:：]?\s*([\d,]+)"))
    conf["supply"] = 0.96 if supply is not None else CONF_NONE
    conf["vat"] = 0.96 if vat is not None else CONF_NONE
    conf["total"] = 0.96 if total is not None else CONF_NONE

    return Quote(
        vendor=Vendor(name=name, biz_no=biz, ceo=ceo),
        quote_date=quote_date, valid_days=valid_days,
        items=_parse_item_rows(t),
        supply=supply, vat=vat, total=total,
        tax_free=bool(re.search(r"면\s*세", t)),
        confidence=conf, source=source,
    )


def _search(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(1) if m else None


# ------------------------------------------------------------------ 기안

def draft_from_text(text: str, source: Optional[str] = None) -> Optional[Draft]:
    if not (text or "").strip():
        return None
    t = _norm(text)
    conf: Dict[str, float] = {}

    def g(key, pats):
        v, c = _grab(t, pats)
        conf[key] = c
        return v

    doc_no = g("doc_no", [r"문서\s*번호\s*[:：]?\s*([^\n]+)"])
    title = g("title", [r"제\s*목\s*[:：]?\s*([^\n]+)"])
    project = g("project", [r"사\s*업\s*명\s*[:：]?\s*([^\n]+)"])
    budget_code = g("budget_code", [r"예산\s*과목\s*[:：]?\s*([^\n]+)"])
    method = g("method", [r"계약\s*방법\s*[:：]?\s*([^\n]+)"])
    drafter = g("drafter", [r"기안자\s*[:：]?\s*([^\n]+)"])

    dd = g("draft_date", [r"기안\s*일자?\s*[:：]?\s*([^\n]+)"])
    draft_date = parse_date(dd)
    if not draft_date:
        conf["draft_date"] = CONF_NONE

    amt = g("amount", [r"소요\s*예산\s*[:：]?\s*(?:금\s*)?([^\n]+)",
                       r"지출\s*(?:예정)?액\s*[:：]?\s*(?:금\s*)?([^\n]+)"])
    amount = parse_money(amt)
    if amount is None:
        conf["amount"] = CONF_NONE

    est = g("estimated", [r"추정\s*가격\s*[:：]?\s*([\d,]+)"])
    estimated = parse_money(est)
    if estimated is None:
        conf["estimated"] = CONF_NONE

    due = g("due_date", [r"납품\s*기한\s*[:：]?\s*([^\n]+)"])
    due_date = parse_date(due)
    if not due_date:
        conf["due_date"] = CONF_NONE

    vend_raw = g("vendor", [r"업\s*체\s*[:：]?\s*([^\n]+)"])
    vendor_name = re.sub(r"\s*[(（][^)）]*[)）]\s*$", "", vend_raw).strip() if vend_raw else None
    if not vendor_name:
        conf["vendor"] = CONF_NONE
    biz = normalize_biz_no(_search(t, r"(\d{3}-\d{2}-\d{5})"))
    conf["biz_no"] = 0.95 if biz else CONF_NONE

    m = re.search(r"(\d)\s*인\s*(?:이상\s*)?견적", t)
    quote_count = int(m.group(1)) if m else (2 if re.search(r"2인\s*이상", t) else None)

    special = bool(re.search(r"(여성기업|장애인기업|사회적\s*경제\s*기업|사회적기업)", t))

    return Draft(doc_no=doc_no, title=title, project=project,
                 budget_code=budget_code, method=method, quote_count=quote_count,
                 drafter=drafter, draft_date=draft_date, amount=amount,
                 estimated=estimated, due_date=due_date,
                 vendor=Vendor(name=vendor_name, biz_no=biz, special_entity=special),
                 confidence=conf, source=source)


# ------------------------------------------------------------ 세금계산서

def tax_from_text(text: str, source: Optional[str] = None) -> Optional[TaxInvoice]:
    if not (text or "").strip():
        return None
    t = _norm(text)
    conf: Dict[str, float] = {}

    approval, conf["approval_no"] = _grab(t, [r"승인\s*번호\s*[:：]?\s*([^\n]+)"])
    wd, conf["write_date"] = _grab(t, [r"작성\s*일자?\s*[:：]?\s*([^\n]+)"])
    write_date = parse_date(wd)
    if not write_date:
        conf["write_date"] = CONF_NONE
    name, conf["vendor"] = _grab(t, [r"(?:공급자|상\s*호)\s*[:：]?\s*([^\n]+)"])
    biz = normalize_biz_no(_search(t, r"(\d{3}-\d{2}-\d{5})"))
    conf["biz_no"] = 0.95 if biz else CONF_NONE

    supply = parse_money(_search(t, r"공급\s*가액\s*[:：]?\s*([\d,]+)"))
    vat = parse_money(_search(t, r"세\s*액\s*[:：]?\s*([\d,]+)"))
    total = parse_money(_search(t, r"(?:합\s*계\s*금?액?|총\s*액)\s*[:：]?\s*([\d,]+)"))
    conf["supply"] = 0.95 if supply is not None else CONF_NONE
    conf["vat"] = 0.95 if vat is not None else CONF_NONE
    conf["total"] = 0.95 if total is not None else CONF_NONE

    return TaxInvoice(approval_no=approval, write_date=write_date,
                      vendor=Vendor(name=name, biz_no=biz),
                      supply=supply, vat=vat, total=total,
                      confidence=conf, source=source)


# ====================================================================
# LLM 경로 — JSON Schema + dict → 모델
# ====================================================================

QUOTE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "required": ["vendor", "items"],
    "additionalProperties": False,
    "properties": {
        "vendor": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": ["string", "null"]},
                "biz_no": {"type": ["string", "null"], "description": "000-00-00000"},
                "ceo": {"type": ["string", "null"]},
                "contact": {"type": ["string", "null"]},
            },
        },
        "quote_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "valid_days": {"type": ["integer", "null"]},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "qty", "unit_price", "amount"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "spec": {"type": ["string", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "qty": {"type": "number"},
                    "unit_price": {"type": "integer"},
                    "amount": {"type": "integer"},
                    "confidence": {"type": ["number", "null"]},
                },
            },
        },
        "supply": {"type": ["integer", "null"]},
        "vat": {"type": ["integer", "null"]},
        "total": {"type": ["integer", "null"]},
        "tax_free": {"type": ["boolean", "null"]},
        "confidence": {"type": ["object", "null"], "additionalProperties": {"type": "number"}},
    },
}

DRAFT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "doc_no": {"type": ["string", "null"]},
        "title": {"type": ["string", "null"]},
        "project": {"type": ["string", "null"]},
        "budget_code": {"type": ["string", "null"]},
        "method": {"type": ["string", "null"]},
        "quote_count": {"type": ["integer", "null"]},
        "drafter": {"type": ["string", "null"]},
        "draft_date": {"type": ["string", "null"]},
        "amount": {"type": ["integer", "null"]},
        "estimated": {"type": ["integer", "null"]},
        "due_date": {"type": ["string", "null"]},
        "vendor": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": ["string", "null"]},
                "biz_no": {"type": ["string", "null"]},
                "special_entity": {"type": ["boolean", "null"]},
            },
        },
        "confidence": {"type": ["object", "null"], "additionalProperties": {"type": "number"}},
    },
}

TAX_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "approval_no": {"type": ["string", "null"]},
        "write_date": {"type": ["string", "null"]},
        "vendor": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": ["string", "null"]},
                "biz_no": {"type": ["string", "null"]},
            },
        },
        "supply": {"type": ["integer", "null"]},
        "vat": {"type": ["integer", "null"]},
        "total": {"type": ["integer", "null"]},
        "confidence": {"type": ["object", "null"], "additionalProperties": {"type": "number"}},
    },
}

EXTRACTION_PROMPT = """당신은 학교회계 서류에서 데이터를 추출하는 도구입니다.
계산하지 마십시오. 판단하지 마십시오. 문서에 적힌 것만 옮기십시오.

규칙:
1. 문서에 없는 값은 절대 만들지 말고 null 을 넣으십시오. 특히 사업자등록번호와 날짜.
2. 금액은 원문 숫자 그대로. 단위 변환·반올림·합계 계산을 하지 마십시오.
   (공급가액이 문서에 없으면 품목을 더하지 말고 null)
3. 표가 깨져 보여도 열 순서(품명/규격/단위/수량/단가/금액)를 유지하십시오.
4. 손글씨·도장·스캔 열화로 불확실한 값은 confidence 를 0.5 이하로 표시하십시오.
5. 날짜는 YYYY-MM-DD 로만 출력하십시오.
6. 반드시 주어진 JSON 스키마로만 응답하십시오."""


def _vendor_from_dict(d: Optional[Dict[str, Any]]) -> Vendor:
    d = d or {}
    return Vendor(name=d.get("name"),
                  biz_no=normalize_biz_no(d.get("biz_no")),
                  ceo=d.get("ceo"), contact=d.get("contact"),
                  special_entity=bool(d.get("special_entity")))


def quote_from_dict(d: Dict[str, Any], source: Optional[str] = None) -> Quote:
    return Quote(
        vendor=_vendor_from_dict(d.get("vendor")),
        quote_date=parse_date(d.get("quote_date")),
        valid_days=d.get("valid_days"),
        items=[Item(name=i["name"], spec=i.get("spec") or "", unit=i.get("unit") or "",
                    qty=i["qty"], unit_price=i["unit_price"], amount=i["amount"],
                    confidence=i.get("confidence") or 1.0)
               for i in d.get("items", [])],
        supply=parse_money(d.get("supply")),
        vat=parse_money(d.get("vat")),
        total=parse_money(d.get("total")),
        tax_free=bool(d.get("tax_free")),
        confidence=d.get("confidence") or {},
        source=source,
    )


def draft_from_dict(d: Dict[str, Any], source: Optional[str] = None) -> Draft:
    return Draft(
        doc_no=d.get("doc_no"), title=d.get("title"), project=d.get("project"),
        budget_code=d.get("budget_code"), method=d.get("method"),
        quote_count=d.get("quote_count"), drafter=d.get("drafter"),
        draft_date=parse_date(d.get("draft_date")),
        amount=parse_money(d.get("amount")),
        estimated=parse_money(d.get("estimated")),
        due_date=parse_date(d.get("due_date")),
        vendor=_vendor_from_dict(d.get("vendor")),
        confidence=d.get("confidence") or {}, source=source,
    )


def tax_from_dict(d: Dict[str, Any], source: Optional[str] = None) -> TaxInvoice:
    return TaxInvoice(
        approval_no=d.get("approval_no"),
        write_date=parse_date(d.get("write_date")),
        vendor=_vendor_from_dict(d.get("vendor")),
        supply=parse_money(d.get("supply")),
        vat=parse_money(d.get("vat")),
        total=parse_money(d.get("total")),
        confidence=d.get("confidence") or {}, source=source,
    )
