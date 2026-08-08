# jeongsan

학교회계 **목적사업비 정산 검증 엔진**. 기안(품의)과 견적서를 넣으면 16개 룰로 검산하고
집행내역서·검증 리포트·잔액 반납 기안 초안을 만든다.

```
pip install -e ".[all]"
```

## 설계 원칙

> **LLM은 추출만 한다. 계산과 검증은 코드가 한다.**

이 패키지에는 네트워크 호출이 없다. 룰은 전부 결정론적이라 테스트 가능하고,
같은 입력에는 언제나 같은 판정이 나온다. LLM은 흐린 스캔본에서 필드를 뽑는
단계에만 쓰고, 그 결과는 `quote_from_dict()` 로 받는다.

## 30초 사용법

```python
from jeongsan import load_project, draft_from_text, quote_from_text, validate

project = load_project("사업.json")
result = validate(
    project,
    draft_from_text(open("기안.txt", encoding="utf-8").read()),
    quote_from_text(open("견적서.txt", encoding="utf-8").read()),
    line="검사·상담 자료 구입",
)

print(result.ok)            # False
for f in result.errors:
    print(f)
# ✕ R01 품목 「상담실 방음패널」 금액 불일치
#       24 × 22,000 = 528,000 인데 견적서에는 550,000
# ✕ R09 계약방법 부적정
#       추정가격 24,000,000원은 수의계약(2인 이상 견적) 대상 …
```

## 명령줄

```bash
# 검증 + 산출물 생성 (오류가 있으면 종료코드 1 → 자동화에 그대로 물릴 수 있다)
jeongsan check --project 사업.json \
               --draft 기안.txt --quote 견적서.txt --tax 세금계산서.txt \
               --line "검사·상담 자료 구입" \
               --html 리포트.html --xlsx 집행내역서.xlsx

jeongsan check ... --json          # 기계 판독용 출력
jeongsan rules                     # 룰 16개 목록
jeongsan schema quote              # LLM 추출용 JSON Schema
jeongsan schema --prompt           # 추출 프롬프트
jeongsan bizno 214-88-01232        # 사업자등록번호 체크섬
```

## 검증 룰

| 코드 | 구분 | 내용 | 심각도 |
|---|---|---|---|
| R01 | 산술 | 품목별 수량 × 단가 = 금액 | error |
| R02 | 산술 | Σ품목금액 = 공급가액 | error |
| R03 | 산술 | 부가세 = 공급가액 × 세율 (면세 시 생략) | error |
| R04 | 산술 | 공급가액 + 부가세 = 합계 | error |
| R05 | 산술 | 기안·견적·세금계산서 3자 대조 | error |
| R06 | 예산 | Σ집행액 ≤ 교부액 | error |
| R07 | 예산 | 세부항목별 집행액 ≤ 배정액 | error |
| R08 | 예산 | 통계목 적정성 (일반운영비 ↔ 자산취득비) | warning |
| R09 | 계약 | 추정가격별 계약방법 (1인/2인 이상/입찰) | error |
| R10 | 계약 | 여성·장애인·사회적경제기업 특례 안내 | info |
| R11 | 계약 | 낙찰하한율 참고값 | info |
| R12 | 계약 | 견적 유효기간 내 계약 체결 | error |
| R13 | 기간 | 모든 일자가 사업 집행기간 내 | error |
| R14 | 식별 | 사업자등록번호 체크섬·서류 간 일치 | error |
| R15 | 징후 | 분할수의(쪼개기) 의심 | warning |
| R16 | 징후 | 목적 외 사용 검토 대상 (LLM 판정 영역) | info |

R15·R16은 **경고만 하고 차단하지 않는다.** 정당한 사유가 있는 경우가 많고,
자동화가 담당자를 막아서면 시스템을 우회하기 시작한다.

## 임계값은 코드에 없다

계약 기준 금액·하한율은 교육청과 개정 시기에 따라 다르다. 전부 `Config`에 있고
YAML로 갈아끼운다.

```yaml
# rules.yaml
single_quote_max: 20000000
multi_quote_max: 100000000
special_single_max: 50000000
lower_rate_under: 0.90
lower_rate_over: 0.88
vat_rounding: floor        # 부가세 원 단위 절사
split_watch_days: 30
```

```bash
jeongsan check --config rules.yaml ...
```

```python
from jeongsan import Config, validate
validate(project, draft, quote, config=Config(single_quote_max=50_000_000))
```

## 사업 마스터 JSON

```json
{
  "name": "2026 학생 정서행동 지원사업",
  "grant_no": "학생건강정책과-2311(2026.2.20.)",
  "grant": 12000000,
  "start": "2026-03-01",
  "end": "2026-11-30",
  "purpose": "학생 정서행동 특성검사 후속 지원",
  "lines": [
    { "name": "검사·상담 자료 구입", "allocated": 6000000 },
    { "name": "소모품 구입", "allocated": 2000000 }
  ],
  "prior": [
    { "date": "2026-03-02", "vendor": "(주)마음교구",
      "biz_no": "214-88-01232", "amount": 1980000,
      "line": "검사·상담 자료 구입", "method": "수의(1인)" }
  ]
}
```

`prior`(기집행 내역)는 예산 잔액 계산과 분할수의 판정에 함께 쓰인다.

## LLM 추출 경로

스캔본·팩스 견적서는 정규식으로 안 된다. 이때만 LLM을 쓴다.

```python
from jeongsan import QUOTE_SCHEMA, EXTRACTION_PROMPT, quote_from_dict

# 1) 아무 모델에나 EXTRACTION_PROMPT + QUOTE_SCHEMA 로 구조화 출력 요청
data = call_your_llm(image, system=EXTRACTION_PROMPT, schema=QUOTE_SCHEMA)

# 2) 결과 dict 를 그대로 모델로
quote = quote_from_dict(data)

# 3) 이후는 텍스트 경로와 완전히 동일
result = validate(project, draft, quote)
```

추출 프롬프트의 핵심 규칙은 세 가지다.

1. 문서에 없는 값은 만들지 말고 `null`
2. 금액은 원문 그대로 — 합계를 대신 계산하지 말 것
3. 불확실하면 `confidence` 를 낮게

## 신뢰도 다루기

`Quote.confidence` / `Draft.confidence` 에 필드별 값이 들어온다.
`Config.conf_auto`(0.95) 이상은 자동 확정, `conf_review`(0.70) 미만은 수기 입력으로
돌리면 20건짜리 정산에서 사람이 볼 항목이 3~5개로 줄어든다.

## 알아둘 것

- `round()` 를 쓰지 않는다. 파이썬 기본 반올림은 은행가 반올림이라
  `round(100000.5) == 100000` 이다. 회계에서 기대하는 동작이 아니다.
  대신 `round_won(x, "half_up" | "floor" | "ceil")` 을 쓴다.
- 나이스·K-에듀파인은 외부 쓰기 API가 없다. 이 패키지는 결재 시스템을
  대체하지 않고, 결재 **전** 검산과 결재 **후** 정산서 작성을 담당한다.
- 최종 확정과 결재는 사람이 한다. 산출물에 그 사실이 명시된다.

## 테스트

```bash
pip install -e ".[dev]"
pytest -q          # 97 passed
```

룰을 추가할 때는 `rules.py` 에 `@rule("R17", "설명")` 데코레이터를 붙인 순수 함수를
쓰고, `tests/test_rules.py` 에 "잡아야 할 것"과 "통과시켜야 할 것" 두 개를 함께 넣는다.

## 면책

이 도구의 판정은 **참고용 초안**입니다. 계약 기준 금액·낙찰하한율·증빙 요건은
교육청과 연도에 따라 다르며 개정됩니다. 반드시 소속 교육청 집행지침으로 확인하십시오.
정산의 최종 확정과 결재 책임은 담당자에게 있습니다.

## 라이선스

MIT
