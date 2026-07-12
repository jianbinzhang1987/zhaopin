import re
from typing import Any

from django.db.models import Max

from apps.recruitment.models import DevelopmentTask, RegularQuestionSet
from services.llm_client import LLMClient, LLMError, with_ai_metadata


def _next_version(queryset) -> int:
    current = queryset.aggregate(max_version=Max("version"))["max_version"] or 0
    return current + 1


def _fallback_analysis(task) -> dict[str, Any]:
    description = task.position.raw_job_description
    resume_text = task.resume.resume_text if task.resume else ""
    skills = [
        {
            "skill": "岗位核心技术",
            "judgment": "needs_verification",
            "confidence": "medium",
            "evidence": resume_text[:160] if resume_text else "简历文本待补充",
            "suggested_method": "qa_question",
        },
        {
            "skill": "项目落地能力",
            "judgment": "needs_verification",
            "confidence": "medium",
            "evidence": description[:160],
            "suggested_method": "development_task",
        },
    ]
    return {
        "position_skills": [{"name": "岗位核心技术", "weight": 50}, {"name": "项目落地能力", "weight": 50}],
        "resume_profile": {"summary": resume_text[:300] if resume_text else "等待PDF解析结果"},
        "skill_matches": skills,
        "verification_items": [
            {"name": item["skill"], "priority": "P0", "selected_method": item["suggested_method"], "reason": item["evidence"]}
            for item in skills
        ],
    }


def build_analysis(task, llm: LLMClient | None = None) -> dict[str, Any]:
    fallback = _fallback_analysis(task)
    resume_text = task.resume.resume_text if task.resume else ""
    if not llm:
        return with_ai_metadata(fallback, "local_fallback")

    system_prompt = (
        "你是智能招聘评测系统的岗位与简历分析专家。"
        "只返回JSON对象，不要输出Markdown。"
        "JSON字段必须包含 position_skills, resume_profile, skill_matches, verification_items。"
        "judgment只能使用 basically_matched, needs_verification, description_doubtful, not_mentioned, irrelevant。"
        "suggested_method只能使用 basic_question, qa_question, development_task, interview_followup。"
    )
    user_prompt = f"""
岗位名称：{task.position.name}
岗位级别：{task.position.get_job_level_display()}
岗位要求：
{task.position.raw_job_description}

候选人：{task.candidate.name}
简历文本：
{resume_text[:8000]}

请输出：
1. position_skills: 岗位能力项数组，每项含 name/category/requirement_level/weight/must_verify/description
2. resume_profile: 候选人画像，含 summary/work_years/education/current_company
3. skill_matches: 能力匹配数组，每项含 skill/judgment/confidence/evidence/suggested_method/risk
4. verification_items: 待验证项数组，每项含 name/priority/selected_method/reason/evidence
"""
    try:
        payload = llm.json_completion(system_prompt, user_prompt)
        return with_ai_metadata(_merge_analysis_payload(payload, fallback), "llm")
    except LLMError as exc:
        fallback["_ai_error"] = str(exc)
        return with_ai_metadata(fallback, "local_fallback_after_error")


def _merge_analysis_payload(payload: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_skills": payload.get("position_skills") or fallback["position_skills"],
        "resume_profile": payload.get("resume_profile") or fallback["resume_profile"],
        "skill_matches": payload.get("skill_matches") or fallback["skill_matches"],
        "verification_items": payload.get("verification_items") or fallback["verification_items"],
    }


def _fallback_questions(task) -> list[dict[str, Any]]:
    return [
        {
            "type": "qa",
            "skill": "岗位核心技术",
            "difficulty": "middle",
            "content": "请结合你的项目经历，说明你如何定位并解决一次线上性能或稳定性问题。",
            "reference_answer": "应覆盖问题定位、指标观察、根因分析、修复方案和复盘。",
            "scoring_points": ["问题拆解", "证据与指标", "方案权衡", "复盘沉淀"],
        },
        {
            "type": "qa",
            "skill": "项目落地能力",
            "difficulty": "middle",
            "content": "请描述一次你主导或深度参与的复杂需求交付过程，以及你如何处理不确定性。",
            "reference_answer": "应体现需求澄清、技术设计、协作推进、风险控制和结果验证。",
            "scoring_points": ["需求理解", "技术方案", "协作推进", "风险意识"],
        },
    ]


def generate_regular_questions(task, llm: LLMClient | None = None) -> RegularQuestionSet:
    questions = _fallback_questions(task)
    source = "local_fallback"
    ai_error = ""
    resume_text = task.resume.resume_text if task.resume else ""
    if llm:
        system_prompt = (
            "你是技术评测出题专家。只返回JSON对象。"
            "JSON必须包含 questions 数组。每道题含 type, skill, difficulty, content, reference_answer, scoring_points。"
            "题目必须围绕待验证能力和简历证据，不要生成与岗位无关的题。"
        )
        analysis = getattr(task, "analysis", None)
        user_prompt = f"""
岗位：{task.position.name}
岗位要求：
{task.position.raw_job_description}

候选人简历摘要：
{resume_text[:5000]}

待验证项：
{getattr(analysis, "verification_items", [])}

请生成普通题：基础技能验证不超过5题，问答题不超过8题。
"""
        try:
            payload = llm.json_completion(system_prompt, user_prompt)
            questions = payload.get("questions") or questions
            source = "llm"
        except LLMError as exc:
            source = "local_fallback_after_error"
            ai_error = str(exc)

    questions = _normalize_questions(questions, source, ai_error)
    version = _next_version(task.regular_question_sets)
    return RegularQuestionSet.objects.create(
        task=task,
        version=version,
        status="reviewing",
        questions=questions,
    )


def generate_regular_question_variant(task, question: dict[str, Any], mode: str, llm: LLMClient | None = None) -> dict[str, Any]:
    mode_text = {
        "simplify": "降低难度，保留同一能力点，让题目更适合基础验证。",
        "increase": "提高难度，保留同一能力点，增加真实项目分析和权衡要求。",
        "replace": "换一道验证同一能力点的新题，避免与原题表达和答案重复。",
    }.get(mode, "换一道验证同一能力点的新题。")
    fallback = dict(question)
    fallback["status"] = "pending"
    fallback["_ai_source"] = "local_fallback"
    if mode == "simplify":
        fallback["difficulty"] = "easy"
        fallback["content"] = f"请用简洁语言回答：{question.get('content', '')}"
    elif mode == "increase":
        fallback["difficulty"] = "hard"
        fallback["content"] = f"请结合真实项目经验深入分析：{question.get('content', '')}"
    elif mode == "replace":
        fallback["content"] = f"请围绕“{question.get('skill') or '当前能力'}”重新设计一个验证问题，并结合候选人项目经历作答。"

    if not llm:
        return _normalize_questions([fallback], "local_fallback")[0]

    resume_text = task.resume.resume_text if task.resume else ""
    system_prompt = (
        "你是技术评测出题专家。只返回JSON对象，不要输出Markdown。"
        "JSON必须包含 question 对象。question 必须包含 type, skill, difficulty, content, reference_answer, scoring_points。"
    )
    user_prompt = f"""
岗位：{task.position.name}
岗位要求：
{task.position.raw_job_description}

候选人简历摘要：
{resume_text[:5000]}

原题：
{question}

调整要求：
{mode_text}
"""
    try:
        payload = llm.json_completion(system_prompt, user_prompt)
        candidate = payload.get("question") or payload
        return _normalize_questions([candidate], "llm")[0]
    except (LLMError, IndexError) as exc:
        fallback["_ai_source"] = "local_fallback_after_error"
        fallback["_ai_error"] = str(exc)
        return _normalize_questions([fallback], "local_fallback_after_error", str(exc))[0]


def _normalize_questions(questions: Any, source: str, ai_error: str = "") -> list[dict[str, Any]]:
    if not isinstance(questions, list):
        return _normalize_questions(_fallback_questions(None), "local_fallback")
    normalized = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        q = {
            "type": item.get("type") or "qa",
            "skill": item.get("skill") or "待验证能力",
            "difficulty": item.get("difficulty") or "middle",
            "content": item.get("content") or "",
            "reference_answer": item.get("reference_answer") or "",
            "scoring_points": item.get("scoring_points") if isinstance(item.get("scoring_points"), list) else [],
            "status": item.get("status") or "pending",
            "_ai_source": item.get("_ai_source") or source,
        }
        if ai_error:
            q["_ai_error"] = ai_error
        if q["content"]:
            normalized.append(q)
    return normalized


def _fallback_development_content(task, direction: str = "") -> dict[str, Any]:
    title = f"{direction}现场开发题" if direction else "岗位能力现场开发题"
    return {
        "title": title,
        "goal": "围绕岗位核心能力完成一个小型可运行模块。",
        "direction": direction,
        "background": "围绕招聘岗位的真实业务场景完成一个小型可运行模块。",
        "requirements": [
            "实现清晰的数据结构和核心流程",
            "提供必要的输入校验和错误处理",
            "提交README说明设计思路、运行方式和取舍",
        ],
        "duration": "2-3天",
        "constraints": {"allow_internet": True, "allow_llm": True},
        "deliverables": ["源码压缩包", "README", "关键截图或录屏"],
        "acceptance_criteria": ["功能完整度", "代码可维护性", "问题拆解能力", "工程表达"],
    }


def _split_numbered_text(value: str) -> list[str]:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    items: list[str] = []
    current = ""
    for line in lines:
        if line.startswith(("-", "*", "•")):
            marker_item = line.lstrip("-*•").strip()
            if marker_item:
                if current:
                    items.append(current)
                current = marker_item
            continue
        if re.match(r"^\d+[\.、)]\s*", line):
            if current:
                items.append(current)
            current = re.sub(r"^\d+[\.、)]\s*", "", line).strip()
        elif current and (line.startswith(("   ", "\t")) or line[:1] in {"-", "—"}):
            current = f"{current} {line.lstrip('-—').strip()}".strip()
        elif current:
            current = f"{current} {line}".strip()
        else:
            current = line
    if current:
        items.append(current)
    return items or ([value.strip()] if value.strip() else [])


def _ensure_list(value: Any, fallback: list[str] | None = None) -> list[Any]:
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, tuple):
        return [item for item in value if item not in (None, "")]
    if isinstance(value, str):
        return _split_numbered_text(value)
    if value:
        return [value]
    return list(fallback or [])


def normalize_development_content(content: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(content)
    normalized["requirements"] = _ensure_list(normalized.get("requirements"))
    normalized["deliverables"] = _ensure_list(normalized.get("deliverables"))
    normalized["acceptance_criteria"] = _ensure_list(normalized.get("acceptance_criteria"))
    constraints = normalized.get("constraints")
    if isinstance(constraints, str):
        normalized["constraints"] = {
            "allow_internet": "不允许联网" not in constraints and "禁止联网" not in constraints,
            "allow_llm": "不允许使用大模型" not in constraints and "禁止使用大模型" not in constraints,
            "notes": _split_numbered_text(constraints),
        }
    elif isinstance(constraints, list):
        normalized["constraints"] = {"allow_internet": True, "allow_llm": True, "notes": constraints}
    elif not isinstance(constraints, dict):
        normalized["constraints"] = {"allow_internet": True, "allow_llm": True}
    return normalized


def generate_development_task(task, llm: LLMClient | None = None, direction: str = "") -> DevelopmentTask:
    content = _fallback_development_content(task, direction)
    source = "local_fallback"
    resume_text = task.resume.resume_text if task.resume else ""
    if llm:
        system_prompt = (
            "你是现场开发题设计专家。只返回JSON对象。"
            "JSON必须包含 title, goal, background, requirements, duration, constraints, deliverables, acceptance_criteria。"
            "题目应适合线下完成，不要求在线IDE或自动运行。"
            "题目必须贴合候选人简历证据、岗位要求和指定出题方向。"
        )
        analysis = getattr(task, "analysis", None)
        user_prompt = f"""
岗位：{task.position.name}
岗位要求：
{task.position.raw_job_description}

候选人简历摘要：
{resume_text[:5000]}

待验证项：
{getattr(analysis, "verification_items", [])}

出题方向：
{direction or "由模型根据岗位和待验证项选择最合适方向"}

请生成一个可提前发送给候选人的现场开发题。题目要有明确业务背景、任务要求、交付内容、验收标准和约束条件。
"""
        try:
            payload = llm.json_completion(system_prompt, user_prompt)
            content = {**content, **{key: value for key, value in payload.items() if value}}
            content["direction"] = direction or content.get("direction", "")
            source = "llm"
        except LLMError as exc:
            content["_ai_error"] = str(exc)
            source = "local_fallback_after_error"

    content = normalize_development_content(content)
    content = with_ai_metadata(content, source)
    version = _next_version(task.development_tasks)
    return DevelopmentTask.objects.create(task=task, version=version, status="reviewing", content=content)
