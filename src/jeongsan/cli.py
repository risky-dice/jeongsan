"""명령줄 인터페이스.

    jeongsan check --project 사업.json --draft 기안.txt --quote 견적서.txt
    jeongsan rules
    jeongsan schema quote
    jeongsan bizno 214-88-01232

`check` 는 error 가 하나라도 있으면 종료코드 1 을 돌려준다 (자동화용).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date
from typing import Optional

from . import __version__
from .config import Config, load_config
from .engine import validate
from .extract import (DRAFT_SCHEMA, EXTRACTION_PROMPT, QUOTE_SCHEMA, TAX_SCHEMA,
                      draft_from_dict, draft_from_text, quote_from_dict,
                      quote_from_text, tax_from_dict, tax_from_text)
from .loader import load_project
from .normalize import normalize_biz_no, valid_biz_no
from .report import html_report, refund_draft, text_report, write_xlsx
from .rules import RULES


def _read(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _load_doc(text_path, json_path, from_text, from_dict):
    """--quote(텍스트) 와 --quote-json(LLM 결과) 중 있는 쪽을 쓴다."""
    if json_path:
        with open(json_path, encoding="utf-8") as fh:
            return from_dict(json.load(fh), source=json_path)
    txt = _read(text_path)
    return from_text(txt, source=text_path) if txt else None


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    if dataclasses.is_dataclass(o):
        return dataclasses.asdict(o)
    raise TypeError(type(o))


def cmd_check(a) -> int:
    project = load_project(a.project)
    cfg: Config = load_config(a.config) if a.config else Config()

    draft = _load_doc(a.draft, a.draft_json, draft_from_text, draft_from_dict)
    quote = _load_doc(a.quote, a.quote_json, quote_from_text, quote_from_dict)
    tax = _load_doc(a.tax, a.tax_json, tax_from_text, tax_from_dict)

    result = validate(project, draft, quote, tax,
                      line=a.line, config=cfg,
                      only=a.only.split(",") if a.only else None,
                      skip=a.skip.split(",") if a.skip else None)

    if a.json:
        payload = {
            "project": project.name,
            "summary": result.summary(),
            "ok": result.ok,
            "this_amount": result.this_amount,
            "prior_total": result.prior_total,
            "total_spent": result.total_spent,
            "remaining": result.remaining,
            "line": {"name": result.line_name,
                     "allocated": result.line_allocated,
                     "spent": result.line_spent},
            "findings": [dataclasses.asdict(f) for f in result.sorted_findings],
            "refund_draft": refund_draft(project, result),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
    else:
        print(text_report(result, project))
        rd = refund_draft(project, result)
        if rd and not a.quiet:
            print("\n[ 잔액 반납 기안 초안 ]\n" + rd)

    if a.html:
        with open(a.html, "w", encoding="utf-8") as fh:
            fh.write(html_report(result, project, draft, quote, tax))
        if not a.json:
            print(f"\nHTML 리포트 → {a.html}")
    if a.xlsx:
        write_xlsx(a.xlsx, project, result, draft, quote)
        if not a.json:
            print(f"집행내역서 → {a.xlsx}")

    return 0 if result.ok else 1


def cmd_rules(a) -> int:
    if a.json:
        print(json.dumps([{"code": c, "title": t} for c, t, _ in RULES],
                         ensure_ascii=False, indent=2))
    else:
        for code, title, fn in RULES:
            doc = (fn.__doc__ or "").strip().splitlines()
            print(f"{code}  {title}")
            if doc:
                print(f"      {doc[0]}")
    return 0


def cmd_schema(a) -> int:
    schemas = {"quote": QUOTE_SCHEMA, "draft": DRAFT_SCHEMA, "tax": TAX_SCHEMA}
    if a.prompt:
        print(EXTRACTION_PROMPT)
        return 0
    print(json.dumps(schemas[a.kind], ensure_ascii=False, indent=2))
    return 0


def cmd_bizno(a) -> int:
    ok = valid_biz_no(a.number)
    norm = normalize_biz_no(a.number) or a.number
    print(f"{norm}  {'유효' if ok else '오류 (체크섬 불일치)'}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jeongsan",
        description="학교회계 목적사업비 정산 검증 (결정론적 룰 엔진)")
    p.add_argument("--version", action="version", version=f"jeongsan {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="집행 건 검증 + 정산서 생성")
    c.add_argument("--project", required=True, help="사업 마스터 JSON/YAML")
    c.add_argument("--draft", help="기안 텍스트 파일")
    c.add_argument("--quote", help="견적서 텍스트 파일")
    c.add_argument("--tax", help="세금계산서 텍스트 파일")
    c.add_argument("--draft-json", help="LLM 추출 결과 JSON (기안)")
    c.add_argument("--quote-json", help="LLM 추출 결과 JSON (견적서)")
    c.add_argument("--tax-json", help="LLM 추출 결과 JSON (세금계산서)")
    c.add_argument("--line", help="세부항목명")
    c.add_argument("--config", help="임계값 설정 YAML/JSON")
    c.add_argument("--only", help="실행할 룰 코드 (쉼표 구분)")
    c.add_argument("--skip", help="제외할 룰 코드 (쉼표 구분)")
    c.add_argument("--html", help="HTML 리포트 출력 경로")
    c.add_argument("--xlsx", help="집행내역서 xlsx 출력 경로")
    c.add_argument("--json", action="store_true", help="JSON으로 출력")
    c.add_argument("--quiet", action="store_true", help="반납 기안 초안 생략")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("rules", help="룰 목록")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd_rules)

    s = sub.add_parser("schema", help="LLM 추출용 JSON Schema / 프롬프트")
    s.add_argument("kind", nargs="?", default="quote", choices=["quote", "draft", "tax"])
    s.add_argument("--prompt", action="store_true", help="추출 프롬프트 출력")
    s.set_defaults(func=cmd_schema)

    b = sub.add_parser("bizno", help="사업자등록번호 체크섬 검증")
    b.add_argument("number")
    b.set_defaults(func=cmd_bizno)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


def run(argv=None):  # pragma: no cover
    """종료코드까지 전달하는 진입점. zipapp(.pyz) 빌드가 이걸 가리킨다.

    zipapp 이 만드는 __main__.py 는 main() 을 호출만 하고 반환값을 버려서
    오류가 있어도 항상 0 으로 끝난다.
    """
    sys.exit(main(argv))


if __name__ == "__main__":  # pragma: no cover
    run()
