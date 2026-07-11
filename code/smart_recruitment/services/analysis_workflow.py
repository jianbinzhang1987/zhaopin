from apps.recruitment.models import DevelopmentTask, RegularQuestionSet


def build_analysis(task) -> dict:
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


def generate_regular_questions(task) -> RegularQuestionSet:
    questions = [
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
    return RegularQuestionSet.objects.create(task=task, version=1, status="reviewing", questions=questions)


def generate_development_task(task) -> DevelopmentTask:
    content = {
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
    return DevelopmentTask.objects.create(task=task, version=1, status="reviewing", content=content)

