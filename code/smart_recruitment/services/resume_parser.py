from pathlib import Path
from typing import Any

from services.llm_client import LLMClient, LLMError, with_ai_metadata


def extract_pdf_text(path: str) -> str:
    try:
        from PyPDF2 import PdfReader
    except ImportError:
        return ""

    reader = PdfReader(path)
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages).strip()


def summarize_resume_text(text: str, llm: LLMClient | None = None) -> dict[str, Any]:
    """对简历文本做结构化抽取。

    传入 LLMClient 时调用大模型抽取姓名/工作年限/学历/当前公司/当前职位/联系方式/技能标签；
    未传入或调用失败时回退到本地极简统计，保证流程不中断。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    fallback = {
        "text_length": len(text),
        "headline": lines[0] if lines else "",
        "keywords": [],
    }
    if not text or not llm:
        return with_ai_metadata(fallback, "local_fallback")

    system_prompt = (
        "你是智能招聘评测系统的简历解析助手。"
        "只返回JSON对象，不要输出Markdown或多余解释。"
        "从候选人简历文本中抽取结构化信息，无法判断的字段留空字符串或空数组。"
        "education 字段请输出中文原文，例如 本科、硕士、大专；"
        "skills 字段是技能标签数组，最多10个。"
    )
    user_prompt = f"""请从以下简历文本中抽取候选人信息，返回JSON对象，字段如下：
- name: 姓名
- work_years: 工作年限（数字，可带小数；无法判断留空）
- education: 学历（中文，如 本科/硕士/大专）
- current_company: 当前或最近任职公司
- current_position: 当前或最近职位
- email: 邮箱
- mobile: 电话
- skills: 技能标签数组

简历文本：
{text[:8000]}
"""
    try:
        payload = llm.json_completion(system_prompt, user_prompt)
    except LLMError as exc:
        fallback["_ai_error"] = str(exc)
        return with_ai_metadata(fallback, "local_fallback_after_error")

    # 归并 LLM 结果与本地兜底，保留 text_length/headline 作为元数据
    normalized = {
        "name": str(payload.get("name", "") or "").strip(),
        "work_years": _coerce_work_years(payload.get("work_years")),
        "education": str(payload.get("education", "") or "").strip(),
        "current_company": str(payload.get("current_company", "") or "").strip(),
        "current_position": str(payload.get("current_position", "") or "").strip(),
        "email": str(payload.get("email", "") or "").strip(),
        "mobile": str(payload.get("mobile", "") or "").strip(),
        "skills": _coerce_skills(payload.get("skills")),
        "text_length": len(text),
        "headline": lines[0] if lines else "",
    }
    return with_ai_metadata(normalized, "llm")


def _coerce_work_years(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_skills(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        normalized = value.replace(",", "、").replace("，", "、").replace("/", "、")
        return [item.strip() for item in normalized.split("、") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def safe_file_name(path: str) -> str:
    return Path(path).name