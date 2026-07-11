from typing import Any

from django.db.models import Max

from apps.recruitment.models import DevelopmentTask, RegularQuestionSet
from services.llm_client import LLMClient, LLMError, with_ai_metadata


def _next_version(queryset) -> int:
    current = queryset.aggregate(max_version=Max("version"))["max_version"] or 0
    return current + 1


def _fallback_analysis(task) -> dict[str, Any]:
    description = task.position.raw_job_description
    resume_text = task.resume.resume_text
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
{task.resume.resume_text[:8000]}

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
{task.resume.resume_text[:5000]}

待验证项：
{getattr(analysis, "verification_items", [])}

请生成普通题：基础技能验证不超过5题，问答题不超过8题。
"""
        try:
            payload = llm.json_completion(system_prompt, user_prompt)
            questions = payload.get("questions") or questions
            source = "llm"
        except LLMError:
            source = "local_fallback_after_error"

    version = _next_version(task.regular_question_sets)
    return RegularQuestionSet.objects.create(
        task=task,
        version=version,
        status="reviewing",
        questions=with_ai_metadata({"items": questions}, source)["items"],
    )


def _fallback_development_content(task) -> dict[str, Any]:
    return {
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


def generate_development_task(task, llm: LLMClient | None = None) -> DevelopmentTask:
    content = _fallback_development_content(task)
    source = "local_fallback"
    if llm:
        system_prompt = (
            "你是现场开发题设计专家。只返回JSON对象。"
            "JSON必须包含 background, requirements, duration, constraints, deliverables, acceptance_criteria。"
            "题目应适合线下完成，不要求在线IDE或自动运行。"
        )
        analysis = getattr(task, "analysis", None)
        user_prompt = f"""
岗位：{task.position.name}
岗位要求：
{task.position.raw_job_description}

候选人简历摘要：
{task.resume.resume_text[:5000]}

待验证项：
{getattr(analysis, "verification_items", [])}

请生成一个可提前发送给候选人的现场开发题。
"""
        try:
            payload = llm.json_completion(system_prompt, user_prompt)
            content = {**content, **{key: value for key, value in payload.items() if value}}
            source = "llm"
        except LLMError as exc:
            content["_ai_error"] = str(exc)
            source = "local_fallback_after_error"

    content = with_ai_metadata(content, source)
    version = _next_version(task.development_tasks)
    return DevelopmentTask.objects.create(task=task, version=version, status="reviewing", content=content)
