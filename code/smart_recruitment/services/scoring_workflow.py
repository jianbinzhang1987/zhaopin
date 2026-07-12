from decimal import Decimal
from typing import Any

from apps.recruitment.models import Evaluation
from services.llm_client import LLMClient, LLMError, with_ai_metadata

_VALID_RECOMMENDATIONS = {"strong_yes", "yes", "hold", "no"}


def _as_dict(value, default=None) -> dict:
    """把任意 LLM 返回强转成 dict；字符串会被包成 {"summary": <str>} 兜底。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value.strip():
            return default if default is not None else {}
        return {"summary": value.strip()}
    if value is None:
        return default if default is not None else {}
    return {"summary": str(value)}


def _as_list_of_dicts(value) -> list[dict]:
    """把 LLM 返回强转成 list[dict]，供模板 {% for item in ... %} 使用。"""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value] if value else []
    if isinstance(value, str) and value.strip():
        return [{"skill": "综合", "level": "待确认", "evidence": value.strip()}]
    return []


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    return str(value)


def _normalize_recommendation(value) -> str:
    return value if value in _VALID_RECOMMENDATIONS else "hold"


def _submission_snapshot(task, submission_type: str) -> list[dict[str, Any]]:
    rows = []
    for submission in task.submissions.filter(submission_type=submission_type).prefetch_related("attachments"):
        rows.append(
            {
                "submitted_at": submission.submitted_at.isoformat(),
                "integrity_status": submission.integrity_status,
                "notes": submission.notes,
                "files": [attachment.original_name for attachment in submission.attachments.all()],
            }
        )
    return rows


def _fallback_score(task, submission_type: str) -> dict[str, Any]:
    if submission_type == "regular":
        return {
            "score": 78,
            "reason": "基于普通题题目与候选人提交记录生成的兜底建议分，需人工复核。",
            "details": [{"item": "基础技能与问答", "score": 78, "comment": "等待接入真实答卷解析"}],
        }
    return {
        "score": 82,
        "reason": "基于现场开发题提交记录生成的兜底建议分，需人工复核。",
        "details": [{"item": "现场开发题", "score": 82, "comment": "等待接入代码包解析"}],
    }


def score_submission(task, submission_type: str, llm: LLMClient | None = None) -> dict[str, Any]:
    fallback = _fallback_score(task, submission_type)
    if not llm:
        return with_ai_metadata(fallback, "local_fallback")

    question_set = task.regular_question_sets.first()
    dev_task = task.development_tasks.first()
    system_prompt = (
        "你是招聘评测评分助手。只返回JSON对象。"
        "JSON必须包含 score, reason, details。score为0到100数字。"
        "你只能给出建议分，不能做最终录用决定。"
    )
    user_prompt = f"""
评分类型：{submission_type}
岗位：{task.position.name}
候选人：{task.candidate.name}
普通题：{getattr(question_set, "questions", [])}
现场开发题：{getattr(dev_task, "content", {})}
候选人提交记录：{_submission_snapshot(task, submission_type)}

请给出AI建议分、理由和分项评分。
"""
    try:
        payload = llm.json_completion(system_prompt, user_prompt)
        score = payload.get("score", fallback["score"])
        return with_ai_metadata(
            {
                "score": max(0, min(100, float(score))),
                "reason": payload.get("reason") or fallback["reason"],
                "details": payload.get("details") or fallback["details"],
            },
            "llm",
        )
    except (LLMError, TypeError, ValueError) as exc:
        fallback["_ai_error"] = str(exc)
        return with_ai_metadata(fallback, "local_fallback_after_error")


def apply_score(task, submission_type: str, payload: dict[str, Any]) -> Evaluation:
    evaluation, _ = Evaluation.objects.get_or_create(task=task)
    if submission_type == "regular":
        evaluation.regular_score = Decimal(str(payload["score"]))
    else:
        evaluation.development_score = Decimal(str(payload["score"]))

    regular = evaluation.regular_score or Decimal("0")
    development = evaluation.development_score or Decimal("0")
    if evaluation.regular_score is not None and evaluation.development_score is not None:
        evaluation.final_score = (regular * task.regular_weight_percent + development * task.development_weight_percent) / Decimal("100")
    elif evaluation.regular_score is not None:
        evaluation.final_score = regular
    elif evaluation.development_score is not None:
        evaluation.final_score = development

    suggestions = _as_dict(evaluation.ai_suggestion)
    suggestions[f"{submission_type}_score"] = payload
    evaluation.ai_suggestion = suggestions
    evaluation.save()
    return evaluation


def _fallback_report(task) -> dict[str, Any]:
    return {
        "ai_suggestion": {
            "summary": "请结合人工评分确认最终结论。本版本生成结构化报告草稿。",
            "risk_level": "medium",
        },
        "skill_evaluations": [
            {"skill": "岗位核心技术", "level": "待确认", "evidence": "普通题与面试反馈"},
            {"skill": "项目落地能力", "level": "待确认", "evidence": "现场开发题提交"},
        ],
        "strengths": "具备与岗位相关的基础能力和项目表达。",
        "risks": "仍需结合人工面试确认关键项目真实性和工程深度。",
        "recommendation": "hold",
        "report_markdown": f"# {task.candidate.name} 评测报告\n\n请在评分完成后补充优势、风险与推荐结论。",
    }


def build_report(task, llm: LLMClient | None = None) -> dict[str, Any]:
    fallback = _fallback_report(task)
    evaluation = getattr(task, "evaluation", None)
    if not llm:
        return with_ai_metadata(fallback, "local_fallback")

    system_prompt = (
        "你是招聘评测报告助手。只返回JSON对象。"
        "JSON必须包含 ai_suggestion, skill_evaluations, strengths, risks, recommendation, report_markdown。"
        "ai_suggestion 必须是一个对象(如 {\"summary\": \"...\", \"risk_level\": \"low|medium|high\"})。"
        "skill_evaluations 必须是一个数组，每个元素是 {\"skill\": \"能力名\", \"level\": \"熟练|掌握|待确认\", \"evidence\": \"依据\"}。"
        "strengths、risks、report_markdown 必须是字符串。"
        "recommendation 只能是 strong_yes, yes, hold, no。不要做绝对录用决定。"
    )
    user_prompt = f"""
岗位：{task.position.name}
候选人：{task.candidate.name}
岗位要求：
{task.position.raw_job_description}
简历摘要：
{(task.resume.resume_text if task.resume else "")[:5000]}
分析结果：
{getattr(getattr(task, "analysis", None), "skill_matches", [])}
普通题：
{getattr(task.regular_question_sets.first(), "questions", [])}
开发题：
{getattr(task.development_tasks.first(), "content", {})}
评分：
regular_score={getattr(evaluation, "regular_score", None)}
development_score={getattr(evaluation, "development_score", None)}
final_score={getattr(evaluation, "final_score", None)}
ai_suggestion={getattr(evaluation, "ai_suggestion", {})}

请生成可给用人部门确认的最终评测报告草稿。
"""
    try:
        payload = llm.json_completion(system_prompt, user_prompt)
        merged: dict[str, Any] = {**fallback, **{key: value for key, value in payload.items() if value}}
        # 大模型常把 ai_suggestion / skill_evaluations 返回成字符串，
        # 这里强制规整成模板/下游可用的结构，避免写回 JSONField 后页面渲染崩坏。
        merged["ai_suggestion"] = _as_dict(merged.get("ai_suggestion"), fallback["ai_suggestion"])
        merged["skill_evaluations"] = _as_list_of_dicts(merged.get("skill_evaluations")) or fallback["skill_evaluations"]
        merged["strengths"] = _as_text(merged.get("strengths")) or fallback["strengths"]
        merged["risks"] = _as_text(merged.get("risks")) or fallback["risks"]
        merged["recommendation"] = _normalize_recommendation(merged.get("recommendation"))
        merged["report_markdown"] = _as_text(merged.get("report_markdown")) or fallback["report_markdown"]
        return with_ai_metadata(merged, "llm")
    except LLMError as exc:
        fallback["_ai_error"] = str(exc)
        return with_ai_metadata(fallback, "local_fallback_after_error")
