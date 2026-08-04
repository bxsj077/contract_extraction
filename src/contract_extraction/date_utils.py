from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from .models import PageText


DATE_PATTERN = re.compile(
    r"(?P<year>20\d{2})\s*(?:年|[./\-])\s*(?P<month>0?[1-9]|1[0-2])\s*(?:月|[./\-])\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])(?!\d)\s*(?:日|号)?"
)

SIGNATURE_WORDS = re.compile(r"签订日期|签署日期|签章日期|签字盖章|以下无正文|合同签署页|法定代表人|授权代表")
PARTY_PATTERN = re.compile(r"甲方|乙方|丙方|丁方|买方|卖方")


@dataclass(slots=True)
class DateCandidate:
    value: date
    party: str
    file_name: str
    page: int
    context: str
    method: str
    confidence: float | None
    needs_review: bool


def normalize_date(year: str, month: str, day: str) -> date | None:
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def parse_dates(text: str) -> list[tuple[date, int, int]]:
    found: list[tuple[date, int, int]] = []
    for match in DATE_PATTERN.finditer(text):
        parsed = normalize_date(match.group("year"), match.group("month"), match.group("day"))
        if parsed:
            found.append((parsed, match.start(), match.end()))
    return found


def _party_before(text: str, position: int) -> str:
    preceding = text[max(0, position - 320):position]
    matches = list(PARTY_PATTERN.finditer(preceding))
    if not matches:
        return "其他方"
    raw = matches[-1].group(0)
    return {"买方": "甲方", "卖方": "乙方"}.get(raw, raw)


def extract_signing_dates(pages: list[PageText], review_threshold: float = 0.90) -> list[DateCandidate]:
    if not pages:
        return []
    max_page_by_file: dict[str, int] = {}
    for page in pages:
        max_page_by_file[page.file_name] = max(max_page_by_file.get(page.file_name, 0), page.page)

    candidates: list[DateCandidate] = []
    for page in pages:
        is_tail = page.page >= max_page_by_file[page.file_name] - 2
        if not is_tail and not SIGNATURE_WORDS.search(page.text):
            continue
        for parsed, start, end in parse_dates(page.text):
            context = page.text[max(0, start - 180): min(len(page.text), end + 100)].replace("\n", " ")
            if not SIGNATURE_WORDS.search(context):
                continue
            party = _party_before(page.text, start)
            score = page.confidence
            enhanced = page.signature_enhanced or "OCR" in page.method
            needs_review = enhanced and (score is None or score < review_threshold)
            candidates.append(DateCandidate(
                value=parsed,
                party=party,
                file_name=page.file_name,
                page=page.page,
                context=re.sub(r"\s+", " ", context).strip(),
                method=page.method,
                confidence=score,
                needs_review=needs_review,
            ))

    # Remove identical duplicates created by native text + enhanced OCR.
    unique: dict[tuple[str, date, str, int], DateCandidate] = {}
    for item in candidates:
        key = (item.party, item.value, item.file_name, item.page)
        previous = unique.get(key)
        if previous is None or (previous.needs_review and not item.needs_review):
            unique[key] = item
    return sorted(unique.values(), key=lambda item: (item.value, item.party, item.page))


def choose_party_dates(candidates: list[DateCandidate]) -> dict[str, date | None]:
    result: dict[str, date | None] = {"甲方": None, "乙方": None, "其他方": None}
    for candidate in candidates:
        key = candidate.party if candidate.party in result else "其他方"
        if result[key] is None or candidate.value > result[key]:
            result[key] = candidate.value
    return result


def choose_contract_sign_date(candidates: list[DateCandidate], policy: str = "latest_recognized") -> date | None:
    if not candidates:
        return None
    values = sorted({item.value for item in candidates})
    if policy == "earliest_recognized":
        return values[0]
    return values[-1]


def add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_workdays(start: date, days: int) -> date:
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def calculate_end_date(start: date, amount: int, unit: str) -> tuple[date | None, str]:
    if amount < 0:
        return None, "工期数值不能为负数"
    if unit in {"日", "天", "日历日"}:
        return start + timedelta(days=amount), ""
    if unit in {"工作日", "个工作日"}:
        return add_workdays(start, amount), "仅排除周六、周日，未纳入法定节假日调休"
    if unit in {"月", "个月"}:
        return add_months(start, amount), ""
    if unit in {"年", "个年"}:
        return add_months(start, amount * 12), ""
    return None, f"不支持的工期单位：{unit}"
