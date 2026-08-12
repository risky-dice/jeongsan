"""검증 룰 R01 ~ R16.

원칙
----
* **LLM은 추출만, 계산·검증은 여기서.** 이 파일에는 네트워크 호출이 없다.
* 룰은 순수 함수다. Context를 받아 Finding 목록을 돌려준다.
* 임계값은 전부 Config에서 온다. 숫자를 코드에 박지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, Iterable, List, Optional

from .config import Config
from .models import (ERROR, INFO, OK, WARNING, Draft, Finding, Project,
                     Quote, TaxInvoice)
from .normalize import format_money as fm
from .normalize import normalize_vendor_name, round_won, valid_biz_no

__all__ = ["Context", "RULES", "rule", "run_all"]


@dataclass
class Context:
    project: Project
    draft: Optional[Draft] = None
    quote: Optional[Quote] = None
    tax: Optional[TaxInvoice] = None
    config: Config = field(default_factory=Config)
    line_name: str = ""

    # -- 파생값 ------------------------------------------------------
    @property
    def this_amount(self) -> int:
        """이번 집행 건의 금액 (부가세 포함)."""
        for v in (self.draft.amount if self.draft else None,
                  self.quote.total if self.quote else None,
                  self.tax.total if self.tax else None):
            if v is not None:
                return v
        return 0

    @property
    def estimated(self) -> Optional[int]:
        """추정가격 (부가세 제외). 기안에 없으면 견적 공급가액으로 대체."""
        if self.draft and self.draft.estimated is not None:
            return self.draft.estimated
        if self.quote and self.quote.supply is not None:
            return self.quote.supply
        if self.tax and self.tax.supply is not None:
            return self.tax.supply
        return None

    @property
    def special_entity(self) -> bool:
        for v in (self.quote.vendor if self.quote else None,
                  self.draft.vendor if self.draft else None):
            if v and v.special_entity:
                return True
        return False

    @property
    def biz_no(self) -> Optional[str]:
        for v in (self.quote.vendor if self.quote else None,
                  self.draft.vendor if self.draft else None,
                  self.tax.vendor if self.tax else None):
            if v and v.biz_no:
                return v.biz_no
        return None

    @property
    def vendor_name(self) -> Optional[str]:
        for v in (self.quote.vendor if self.quote else None,
                  self.draft.vendor if self.draft else None,
                  self.tax.vendor if self.tax else None):
            if v and v.name:
                return v.name
        return None

    def dates(self) -> List[tuple]:
        out = []
        if self.draft and self.draft.draft_date:
            out.append(("기안일", self.draft.draft_date))
        if self.quote and self.quote.quote_date:
            out.append(("견적일", self.quote.quote_date))
        if self.draft and self.draft.due_date:
            out.append(("납품기한", self.draft.due_date))
        if self.tax and self.tax.write_date:
            out.append(("계산서 작성일", self.tax.write_date))
        return out


RuleFn = Callable[[Context], Iterable[Finding]]
RULES: List[tuple] = []          # [(code, title, fn), ...]


def rule(code: str, title: str):
    """룰 등록 데코레이터."""
    def deco(fn: RuleFn) -> RuleFn:
        RULES.append((code, title, fn))
        fn.code = code            # type: ignore[attr-defined]
        fn.title = title          # type: ignore[attr-defined]
        return fn
    return deco


def _f(code, sev, title, detail=None, **evidence) -> Finding:
    return Finding(code=code, severity=sev, title=title, detail=detail, evidence=evidence)


# =====================================================================
# A. 산술 검증
# =====================================================================

@rule("R01", "품목별 수량 × 단가 검산")
def r01_item_arithmetic(ctx: Context):
    q = ctx.quote
    if not q or not q.items:
        return
    tol = ctx.config.tolerance
    bad = [i for i in q.items if abs(i.computed - i.amount) > tol]
    for i in bad:
        yield _f("R01", ERROR, f"품목 「{i.name}」 금액 불일치",
                 f"{i.qty:g} × {fm(i.unit_price)} = {fm(i.computed)} "
                 f"인데 견적서에는 {fm(i.amount)}",
                 item=i.name, computed=i.computed, stated=i.amount,
                 diff=i.amount - i.computed)
    if not bad:
        yield _f("R01", OK, f"품목 {len(q.items)}건 수량×단가 검산 일치")


@rule("R02", "품목 합계 = 공급가액")
def r02_items_sum(ctx: Context):
    q = ctx.quote
    if not q or not q.items:
        return
    if q.supply is None:
        yield _f("R02", WARNING, "공급가액을 찾지 못함", "수기 입력이 필요합니다.")
        return
    s = q.items_sum
    if abs(s - q.supply) > ctx.config.tolerance:
        yield _f("R02", ERROR, "품목 합계와 공급가액 불일치",
                 f"품목 합계 {fm(s)} ≠ 공급가액 {fm(q.supply)} (차액 {fm(q.supply - s)})",
                 items_sum=s, supply=q.supply)
    else:
        yield _f("R02", OK, f"품목 합계 = 공급가액 ({fm(s)})")


@rule("R03", "부가세 = 공급가액 × 세율")
def r03_vat(ctx: Context):
    q = ctx.quote
    if not q or q.supply is None or q.vat is None:
        return
    if q.tax_free:
        yield _f("R03", INFO, "면세 표기 확인됨 — 부가세 검증 생략")
        return
    expected = round_won(q.supply * ctx.config.vat_rate, ctx.config.vat_rounding)
    if abs(expected - q.vat) > ctx.config.tolerance:
        yield _f("R03", ERROR, "부가세 계산 오류",
                 f"공급가액 {fm(q.supply)} × {ctx.config.vat_rate:.0%} = {fm(expected)} "
                 f"이어야 하는데 {fm(q.vat)}",
                 expected=expected, stated=q.vat)
    else:
        yield _f("R03", OK, f"부가세 정상 ({fm(q.vat)})")


@rule("R04", "공급가액 + 부가세 = 합계")
def r04_total(ctx: Context):
    q = ctx.quote
    if not q or None in (q.supply, q.vat, q.total):
        return
    s = q.supply + q.vat
    if abs(s - q.total) > ctx.config.tolerance:
        yield _f("R04", ERROR, "합계금액 불일치",
                 f"{fm(q.supply)} + {fm(q.vat)} = {fm(s)} ≠ {fm(q.total)}",
                 expected=s, stated=q.total)
    else:
        yield _f("R04", OK, f"합계금액 정상 ({fm(q.total)})")


@rule("R05", "기안·견적·세금계산서 3자 대조")
def r05_cross_check(ctx: Context):
    vals = []
    if ctx.draft and ctx.draft.amount is not None:
        vals.append(("기안", ctx.draft.amount))
    if ctx.quote and ctx.quote.total is not None:
        vals.append(("견적", ctx.quote.total))
    if ctx.tax and ctx.tax.total is not None:
        vals.append(("세금계산서", ctx.tax.total))
    if len(vals) < 2:
        return
    amounts = [v for _, v in vals]
    if max(amounts) - min(amounts) > ctx.config.tolerance:
        yield _f("R05", ERROR, "기안·견적·세금계산서 금액 불일치",
                 "  /  ".join(f"{k} {fm(v)}" for k, v in vals)
                 + f" — 차액 {fm(max(amounts) - min(amounts))}",
                 values=dict(vals))
    else:
        yield _f("R05", OK,
                 f"{'·'.join(k for k, _ in vals)} 금액 일치 ({fm(amounts[0])})")


# =====================================================================
# B. 예산 검증
# =====================================================================

@rule("R06", "교부액 이내 집행")
def r06_grant_limit(ctx: Context):
    p = ctx.project
    total = p.prior_total + ctx.this_amount
    if total > p.grant:
        yield _f("R06", ERROR, "교부액 초과",
                 f"집행 합계 {fm(total)} > 교부액 {fm(p.grant)} "
                 f"(초과 {fm(total - p.grant)})",
                 total=total, grant=p.grant)
    else:
        yield _f("R06", OK, f"교부액 이내 (잔액 {fm(p.grant - total)})")


@rule("R07", "세부항목 배정액 이내 집행")
def r07_line_limit(ctx: Context):
    p, name = ctx.project, ctx.line_name
    if not name:
        # 조용히 건너뛰면 예산 초과 검증이 통째로 사라진 걸 아무도 모른다.
        if p.lines:
            yield _f("R07", WARNING, "세부항목 미지정 — 배정액 검증 생략",
                     "--line 으로 세부항목명을 주면 배정액 초과까지 검증합니다.",
                     lines=[ln.name for ln in p.lines])
        return
    allocated = p.line_allocated(name)
    if allocated is None:
        yield _f("R07", WARNING, f"세부항목 「{name}」이 사업 예산에 없음",
                 "항목명 오기이거나 예산 전용이 필요할 수 있습니다.")
        return
    spent = p.prior_line_total(name) + ctx.this_amount
    if spent > allocated:
        yield _f("R07", ERROR, f"세부항목 「{name}」 배정액 초과",
                 f"{fm(spent)} > {fm(allocated)} — 예산 전용 승인 또는 과목 변경 필요",
                 spent=spent, allocated=allocated)
    else:
        yield _f("R07", OK, f"세부항목 배정액 이내 (잔액 {fm(allocated - spent)})")


@rule("R08", "통계목(자산취득비) 적정성")
def r08_statistic_code(ctx: Context):
    d, q = ctx.draft, ctx.quote
    if not d or not d.budget_code:
        return
    if "일반운영비" not in d.budget_code:
        yield _f("R08", OK, f"예산과목: {d.budget_code}")
        return
    th = ctx.config.asset_unit_price_threshold
    big = [i for i in (q.items if q else []) if i.unit_price >= th]
    if big:
        top = max(big, key=lambda i: i.unit_price)
        yield _f("R08", WARNING, "통계목 재확인 필요",
                 f"단가 {fm(top.unit_price)}원 「{top.name}」이 일반운영비로 편성됨 "
                 f"— 자산취득비 해당 여부 확인",
                 item=top.name, unit_price=top.unit_price, threshold=th)
    else:
        yield _f("R08", OK, f"예산과목: {d.budget_code}")


# =====================================================================
# C. 계약 검증
# =====================================================================

@rule("R09", "추정가격별 계약방법 적정성")
def r09_contract_method(ctx: Context):
    est = ctx.estimated
    if est is None:
        yield _f("R09", WARNING, "추정가격을 찾지 못해 계약방법을 판정할 수 없음")
        return
    need = ctx.config.method_for(est, ctx.special_entity)
    stated_method = (ctx.draft.method if ctx.draft else None) or ""
    stated_quotes = ctx.draft.quote_count if ctx.draft else None

    if need["min_quotes"] is None:                     # 입찰 대상
        if "입찰" in stated_method:
            yield _f("R09", OK, f"계약방법 적정 — {need['method']}")
        else:
            yield _f("R09", ERROR, "계약방법 부적정",
                     f"추정가격 {fm(est)}원은 {need['method']} 대상 "
                     f"— 기안의 계약방법은 「{stated_method or '미기재'}」",
                     estimated=est, required=need["method"])
        return

    if stated_quotes is None:
        yield _f("R09", WARNING, f"견적 인원수 미기재 — {need['method']} 필요",
                 f"추정가격 {fm(est)}원 기준", estimated=est, required=need["method"])
    elif stated_quotes < need["min_quotes"]:
        yield _f("R09", ERROR, "계약방법 부적정",
                 f"추정가격 {fm(est)}원은 {need['method']} 대상 "
                 f"(견적 {need['min_quotes']}인 이상 필요) — 기안은 {stated_quotes}인 견적",
                 estimated=est, required=need["min_quotes"], stated=stated_quotes)
    else:
        yield _f("R09", OK, f"계약방법 적정 — {need['method']}")


@rule("R10", "수의계약 특례 대상 확인")
def r10_special_entity(ctx: Context):
    est, cfg = ctx.estimated, ctx.config
    if est is None or ctx.special_entity:
        return
    if cfg.single_quote_max < est <= cfg.special_single_max:
        yield _f("R10", INFO, "특례 확인 가능",
                 f"여성기업·장애인기업·사회적경제기업이면 "
                 f"{fm(cfg.special_single_max)}원 이하 1인 견적 수의계약 가능",
                 estimated=est)


@rule("R11", "낙찰하한율 참고")
def r11_lower_limit(ctx: Context):
    est = ctx.estimated
    if est is None:
        return
    need = ctx.config.method_for(est, ctx.special_entity)
    if not need["lower_rate"]:
        return
    floor = round_won(est * need["lower_rate"], "half_up")
    yield _f("R11", INFO, f"낙찰하한율 참고: {need['lower_rate']:.0%}",
             f"예정가격 대비 {fm(floor)}원 이상이어야 함",
             rate=need["lower_rate"], floor=floor)


@rule("R12", "견적 유효기간 내 계약 체결")
def r12_quote_validity(ctx: Context):
    q, d = ctx.quote, ctx.draft
    if not q or not d or not q.quote_date or not q.valid_days or not d.draft_date:
        return
    gap = (d.draft_date - q.quote_date).days
    if gap > q.valid_days:
        yield _f("R12", ERROR, "견적 유효기간 경과",
                 f"견적일 {q.quote_date} + {q.valid_days}일 < 기안일 {d.draft_date} "
                 f"(경과 {gap - q.valid_days}일)",
                 gap=gap, valid_days=q.valid_days)
    elif gap < 0:
        yield _f("R12", ERROR, "견적일이 기안일보다 늦음",
                 f"견적일 {q.quote_date} > 기안일 {d.draft_date}", gap=gap)
    else:
        yield _f("R12", OK, f"견적 유효기간 이내 ({gap}일 / {q.valid_days}일)")


# =====================================================================
# D. 기간·식별 검증
# =====================================================================

@rule("R13", "모든 일자가 사업 집행기간 내")
def r13_period(ctx: Context):
    p = ctx.project
    if not p.start or not p.end:
        return
    ds = ctx.dates()
    if not ds:
        return
    outside = [(k, v) for k, v in ds if v < p.start or v > p.end]
    if outside:
        yield _f("R13", ERROR, "집행기간 밖의 날짜",
                 ", ".join(f"{k} {v}" for k, v in outside)
                 + f" — 사업기간 {p.start} ~ {p.end}",
                 outside=[(k, str(v)) for k, v in outside])
    else:
        yield _f("R13", OK, f"모든 일자가 집행기간 내 ({len(ds)}건 확인)")


@rule("R14", "사업자등록번호 체크섬")
def r14_biz_no(ctx: Context):
    seen = []
    for src, v in (("견적서", ctx.quote.vendor if ctx.quote else None),
                   ("기안", ctx.draft.vendor if ctx.draft else None),
                   ("세금계산서", ctx.tax.vendor if ctx.tax else None)):
        if v and v.biz_no and v.biz_no not in [s[1] for s in seen]:
            seen.append((src, v.biz_no))
    if not seen:
        yield _f("R14", WARNING, "사업자등록번호를 찾지 못함")
        return
    for src, no in seen:
        if valid_biz_no(no):
            yield _f("R14", OK, f"사업자등록번호 유효: {no}", f"출처: {src}")
        else:
            yield _f("R14", ERROR, f"사업자등록번호 오류: {no}",
                     f"체크섬 불일치 — OCR 오독 또는 오기 가능성 (출처: {src})",
                     biz_no=no, source=src)
    if len({no for _, no in seen}) > 1:
        yield _f("R14", ERROR, "서류 간 사업자등록번호 불일치",
                 ", ".join(f"{s} {n}" for s, n in seen))


# =====================================================================
# E. 이상 징후 (경고만 — 차단하지 않음)
# =====================================================================

@rule("R15", "분할수의(쪼개기) 의심")
def r15_split_contract(ctx: Context):
    p, cfg = ctx.project, ctx.config
    biz, name = ctx.biz_no, normalize_vendor_name(ctx.vendor_name)
    base_date = (ctx.draft.draft_date if ctx.draft else None) \
        or (ctx.quote.quote_date if ctx.quote else None)
    if not base_date or (not biz and not name):
        return

    def same_vendor(pr) -> bool:
        if biz and pr.biz_no:
            return pr.biz_no == biz
        return bool(name) and normalize_vendor_name(pr.vendor_name) == name

    near = [pr for pr in p.prior
            if pr.spend_date and same_vendor(pr)
            and abs((base_date - pr.spend_date).days) <= cfg.split_watch_days]
    if not near:
        return
    merged = sum(pr.amount for pr in near) + ctx.this_amount
    est = ctx.estimated if ctx.estimated is not None else ctx.this_amount
    before = cfg.method_for(est, ctx.special_entity)["method"]
    after = cfg.method_for(merged, ctx.special_entity)["method"]
    if before != after:
        yield _f("R15", WARNING, "분할수의 의심",
                 f"동일 업체와 {cfg.split_watch_days}일 내 {len(near)}건 "
                 f"(합산 {fm(merged)}) — 합산 시 「{after}」 구간. 정당 사유 기재 권장",
                 count=len(near), merged=merged, before=before, after=after)
    else:
        yield _f("R15", INFO,
                 f"동일 업체 최근 거래 {len(near)}건 (합산 {fm(merged)}) — 구간 변동 없음",
                 count=len(near), merged=merged)


@rule("R16", "목적 외 사용 의심 (LLM 판정 영역)")
def r16_purpose_fit(ctx: Context):
    q = ctx.quote
    if not q or not q.items:
        return
    names = ", ".join(i.name for i in q.items)
    if ctx.project.purpose:
        yield _f("R16", INFO, "목적 적합성 검토 대상",
                 f"품목: {names} / 사업목적: {ctx.project.purpose}",
                 items=[i.name for i in q.items], purpose=ctx.project.purpose)
    else:
        yield _f("R16", INFO, "사업 목적이 등록되지 않아 목적 적합성 판정 생략",
                 f"품목: {names}", items=[i.name for i in q.items])


# =====================================================================

def run_all(ctx: Context, only: Optional[Iterable[str]] = None,
            skip: Optional[Iterable[str]] = None) -> List[Finding]:
    """등록된 룰을 순서대로 실행한다."""
    only_set = set(only) if only else None
    skip_set = set(skip) if skip else set()
    out: List[Finding] = []
    for code, _title, fn in RULES:
        if only_set is not None and code not in only_set:
            continue
        if code in skip_set:
            continue
        out.extend(fn(ctx))
    return out
