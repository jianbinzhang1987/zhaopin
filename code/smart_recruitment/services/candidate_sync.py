import re
from decimal import Decimal, InvalidOperation
from typing import Any


def _clean_text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _decimal_years(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def sync_candidate_from_profile(task, profile: dict[str, Any] | None) -> bool:
    """用简历结构化画像回填候选人主表；仅用非空字段覆盖，避免清掉人工信息。"""
    if not profile:
        return False

    candidate = task.candidate
    changed = False
    field_map = {
        "name": "name",
        "education": "education",
        "current_company": "current_company",
        "current_position": "current_position",
        "email": "email",
        "mobile": "mobile",
    }
    for profile_key, candidate_field in field_map.items():
        value = _clean_text(profile.get(profile_key))
        if value and getattr(candidate, candidate_field) != value:
            setattr(candidate, candidate_field, value)
            changed = True

    years = _decimal_years(profile.get("work_years"))
    if years is not None and candidate.work_years != years:
        candidate.work_years = years
        changed = True

    if changed:
        candidate.save(update_fields=["name", "work_years", "education", "current_company", "current_position", "email", "mobile", "updated_at"])

    task_name = f"{task.position.name} - {candidate.name}"
    if candidate.name and task.task_name != task_name:
        task.task_name = task_name
        task.save(update_fields=["task_name", "updated_at"])
        changed = True

    return changed
