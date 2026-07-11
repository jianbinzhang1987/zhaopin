def build_report(task) -> dict:
    return {
        "ai_suggestion": {
            "summary": "请结合人工评分确认最终结论。本版本仅生成结构化报告草稿。",
            "risk_level": "medium",
        },
        "skill_evaluations": [
            {"skill": "岗位核心技术", "level": "待确认", "evidence": "普通题与面试反馈"},
            {"skill": "项目落地能力", "level": "待确认", "evidence": "现场开发题提交"},
        ],
        "report_markdown": f"# {task.candidate.name} 评测报告\n\n请在评分完成后补充优势、风险与推荐结论。",
    }

