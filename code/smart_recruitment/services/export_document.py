"""从已落库的招聘评测数据生成可下载文档。

支持两类格式：
- docx  使用 python-docx
- pdf   使用 reportlab（中文用 STSong-Late CID 字体）

所有导出函数返回 tuple[str, bytes]：(文件名, 内容字节)，供视图设置下载响应。
"""
from __future__ import annotations

import io
import re
from typing import Any

# reportlab 中文 CID 字体
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from docx import Document

from apps.recruitment.models import DevelopmentTask, RecruitmentTask

# 注册中文 CID 字体一次即可
_CN_FONT = "STSong-Light"
_CN_FONT_REGISTERED = False


def _ensure_cn_font() -> str:
    global _CN_FONT_REGISTERED
    if not _CN_FONT_REGISTERED:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont(_CN_FONT))
        except Exception:
            # 降级：若注册失败则返回 Helvetica，中文会乱码但避免抛错
            return "Helvetica"
        _CN_FONT_REGISTERED = True
    return _CN_FONT


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

_FORBIDDEN_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _safe_filename(text: str, suffix: str) -> str:
    """生成服务化的文件名，去除文件系统非法字符。"""
    cleaned = _FORBIDDEN_CHARS.sub("_", text).strip("_") or "export"
    cleaned = cleaned[:60]
    return f"{cleaned}.{suffix}"


def _title_for(task: RecruitmentTask, kind: str, with_answers: bool) -> str:
    label = {"regular": "普通题", "development": "现场开发题", "report": "评测报告"}[kind]
    edition = "部门版" if (kind != "report" and with_answers) else ("候选人版" if kind == "regular" else "")
    edition_label = f"（{edition}）" if edition else ""
    return f"{task.task_name} {label}{edition_label}"


def _kv_rows(task: RecruitmentTask) -> list[list[str]]:
    return [
        ["任务编号", task.task_no or "-"],
        ["岗位", f"{task.position.name}（{task.position.get_job_level_display()}）"],
        ["候选人", task.candidate.name or "-"],
        ["用人部门", task.department.name if task.department else "-"],
        ["技术负责人", task.technical_owner.get_full_name() or task.technical_owner.username if task.technical_owner else "-"],
        ["生成时间", task.updated_at.strftime("%Y-%m-%d %H:%M") if task.updated_at else "-"],
    ]


def _recommendation_display(rec: str | None) -> str:
    mapping = {"strong_yes": "强烈推荐", "yes": "推荐", "hold": "待定", "no": "不推荐"}
    return mapping.get(rec or "", "待生成")


def _questions_for_export(task: RecruitmentTask) -> list[dict[str, Any]]:
    qs = task.regular_question_sets.order_by("-version").first()
    return qs.questions if qs and isinstance(qs.questions, list) else []


def _dev_task_for_export(task: RecruitmentTask) -> DevelopmentTask | None:
    return task.development_tasks.order_by("-version").first()


# ---------------------------------------------------------------------------
# DOCX 生成
# ---------------------------------------------------------------------------

def build_regular_questions_docx(task: RecruitmentTask, with_answers: bool = True) -> tuple[str, bytes]:
    doc = Document()
    doc.add_heading(_title_for(task, "regular", with_answers), level=1)
    _write_meta_table_docx(doc, task)
    questions = _questions_for_export(task)
    if not questions:
        doc.add_paragraph("暂无题目。")
        return _safe_filename(task.task_no or "regular", "docx"), _docx_bytes(doc)
    for idx, q in enumerate(questions, 1):
        doc.add_heading(f"{idx}. [{q.get('skill', '-')}] {q.get('content', '-')}", level=2)
        meta = f"题型 {q.get('type', '-')} · 难度 {q.get('difficulty', '-')}"
        doc.add_paragraph(meta)
        if with_answers:
            ref = q.get("reference_answer", "")
            if ref:
                doc.add_paragraph("参考答案：", style="Intense Quote")
                doc.add_paragraph(ref)
            points = q.get("scoring_points", [])
            if points:
                doc.add_paragraph("评分要点：")
                for p in points:
                    doc.add_paragraph(p, style="List Bullet")
    filename = _safe_filename(task.task_no or "regular", "docx")
    return filename, _docx_bytes(doc)


def build_development_task_docx(task: RecruitmentTask) -> tuple[str, bytes]:
    doc = Document()
    doc.add_heading(_title_for(task, "development", True), level=1)
    _write_meta_table_docx(doc, task)
    dev = _dev_task_for_export(task)
    content = (dev.content if dev else {}) or {}
    if not content:
        doc.add_paragraph("暂无现场开发题内容。")
        return _safe_filename(task.task_no or "development", "docx"), _docx_bytes(doc)
    _section_paragraph(doc, "业务背景", content.get("background"))
    _section_list(doc, "任务要求", content.get("requirements"))
    _section_paragraph(doc, "开发时长", content.get("duration"))
    _section_dict_list(doc, "约束条件", content.get("constraints"))
    _section_list(doc, "交付内容", content.get("deliverables"))
    _section_list(doc, "验收标准", content.get("acceptance_criteria"))
    filename = _safe_filename(task.task_no or "development", "docx")
    return filename, _docx_bytes(doc)


def build_report_docx(task: RecruitmentTask) -> tuple[str, bytes]:
    doc = Document()
    doc.add_heading(_title_for(task, "report", True), level=1)
    _write_meta_table_docx(doc, task)
    ev = getattr(task, "evaluation", None)
    if not ev:
        doc.add_paragraph("暂无评测报告数据。")
        return _safe_filename(task.task_no or "report", "docx"), _docx_bytes(doc)
    # 评分卡片
    table = doc.add_table(rows=4, cols=2)
    table.style = "Light Grid Accent 1"
    rows = [
        ("普通题得分", str(ev.regular_score) if ev.regular_score is not None else "-"),
        ("现场开发题得分", str(ev.development_score) if ev.development_score is not None else "-"),
        ("综合得分", str(ev.final_score) if ev.final_score is not None else "-"),
        ("建议结论", _recommendation_display(ev.recommendation)),
    ]
    for i, (k, v) in enumerate(rows):
        table.cell(i, 0).text = k
        table.cell(i, 1).text = v
    _section_paragraph(doc, "优势", ev.strengths)
    _section_paragraph(doc, "风险", ev.risks)
    skills = ev.skill_evaluations or []
    if skills:
        doc.add_heading("能力评价", level=2)
        sk_table = doc.add_table(rows=1, cols=3)
        sk_table.style = "Light Grid Accent 1"
        hdr = sk_table.rows[0].cells
        hdr[0].text, hdr[1].text, hdr[2].text = "能力", "评价", "依据"
        for s in skills:
            row = sk_table.add_row().cells
            row[0].text = str(s.get("skill", "-"))
            row[1].text = str(s.get("level", "-"))
            row[2].text = str(s.get("evidence", "-"))
    if ev.report_markdown:
        doc.add_heading("报告正文", level=2)
        for line in ev.report_markdown.splitlines():
            if line.strip():
                doc.add_paragraph(line)
    filename = _safe_filename(task.task_no or "report", "docx")
    return filename, _docx_bytes(doc)


def _write_meta_table_docx(doc: Document, task: RecruitmentTask) -> None:
    table = doc.add_table(rows=0, cols=2)
    table.style = "Light List Accent 1"
    for k, v in _kv_rows(task):
        row = table.add_row().cells
        row[0].text = k
        row[1].text = v
    doc.add_paragraph("")


def _section_paragraph(doc: Document, title: str, value: Any) -> None:
    if value in (None, "", []):
        return
    doc.add_heading(title, level=2)
    doc.add_paragraph(str(value))


def _section_list(doc: Document, title: str, items: Any) -> None:
    if not items:
        return
    doc.add_heading(title, level=2)
    if isinstance(items, str):
        doc.add_paragraph(items)
        return
    for it in items:
        doc.add_paragraph(str(it), style="List Bullet")


def _section_dict_list(doc: Document, title: str, data: Any) -> None:
    if not data:
        return
    doc.add_heading(title, level=2)
    if isinstance(data, dict):
        for k, v in data.items():
            doc.add_paragraph(f"{k}：{v}", style="List Bullet")
    elif isinstance(data, list):
        for it in data:
            doc.add_paragraph(str(it), style="List Bullet")
    else:
        doc.add_paragraph(str(data))


def _docx_bytes(doc: Document) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF 生成
# ---------------------------------------------------------------------------

def _pdf_styles():
    font = _ensure_cn_font()
    styles = getSampleStyleSheet()
    # 继承内置样式但改用中文字体
    normal = ParagraphStyle("cn_normal", parent=styles["Normal"], fontName=font, leading=18)
    title = ParagraphStyle("cn_title", parent=styles["Title"], fontName=font, fontSize=18, leading=24)
    h2 = ParagraphStyle("cn_h2", parent=styles["Heading2"], fontName=font, fontSize=13, leading=18, spaceBefore=10)
    return {"normal": normal, "title": title, "h2": h2, "font": font}


def build_regular_questions_pdf(task: RecruitmentTask, with_answers: bool = True) -> tuple[str, bytes]:
    buf = io.BytesIO()
    styles = _pdf_styles()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    story = _pdf_meta_block(task, styles, kind="regular", with_answers=with_answers)
    questions = _questions_for_export(task)
    if not questions:
        story.append(Paragraph("暂无题目。", styles["normal"]))
    for idx, q in enumerate(questions, 1):
        head = f"{idx}. [{_s(q.get('skill'))}] {_s(q.get('content'))}"
        story.append(Paragraph(head, styles["h2"]))
        story.append(Paragraph(f"题型 {_s(q.get('type'))} · 难度 {_s(q.get('difficulty'))}", styles["normal"]))
        if with_answers:
            ref = q.get("reference_answer")
            if ref:
                story.append(Paragraph("参考答案：", styles["normal"]))
                story.append(Paragraph(_s(ref), styles["normal"]))
            points = q.get("scoring_points") or []
            if points:
                story.append(Paragraph("评分要点：" + "；".join(_s(p) for p in points), styles["normal"]))
        story.append(Spacer(1, 6))
    doc.build(story)
    return _safe_filename(task.task_no or "regular", "pdf"), buf.getvalue()


def build_development_task_pdf(task: RecruitmentTask) -> tuple[str, bytes]:
    buf = io.BytesIO()
    styles = _pdf_styles()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    story = _pdf_meta_block(task, styles, kind="development", with_answers=True)
    dev = _dev_task_for_export(task)
    content = (dev.content if dev else {}) or {}
    if not content:
        story.append(Paragraph("暂无现场开发题内容。", styles["normal"]))
    else:
        _pdf_section(story, styles, "业务背景", content.get("background"))
        _pdf_list(story, styles, "任务要求", content.get("requirements"))
        _pdf_section(story, styles, "开发时长", content.get("duration"))
        if content.get("constraints"):
            data = content["constraints"]
            if isinstance(data, dict):
                text = "；".join(f"{k}：{v}" for k, v in data.items())
            else:
                text = "；".join(str(x) for x in data)
            _pdf_section(story, styles, "约束条件", text)
        _pdf_list(story, styles, "交付内容", content.get("deliverables"))
        _pdf_list(story, styles, "验收标准", content.get("acceptance_criteria"))
    doc.build(story)
    return _safe_filename(task.task_no or "development", "pdf"), buf.getvalue()


def build_report_pdf(task: RecruitmentTask) -> tuple[str, bytes]:
    buf = io.BytesIO()
    styles = _pdf_styles()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    story = _pdf_meta_block(task, styles, kind="report", with_answers=True)
    ev = getattr(task, "evaluation", None)
    if not ev:
        story.append(Paragraph("暂无评测报告数据。", styles["normal"]))
        doc.build(story)
        return _safe_filename(task.task_no or "report", "pdf"), buf.getvalue()
    score_table = Table(
        [
            ["普通题得分", _score(ev.regular_score)],
            ["现场开发题得分", _score(ev.development_score)],
            ["综合得分", _score(ev.final_score)],
            ["建议结论", _recommendation_display(ev.recommendation)],
        ],
        colWidths=[60 * mm, 70 * mm],
    )
    score_table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), styles["font"]), ("GRID", (0, 0), (-1, -1), 0.5, "#ccc")]))
    story.append(score_table)
    story.append(Spacer(1, 10))
    _pdf_section(story, styles, "优势", ev.strengths)
    _pdf_section(story, styles, "风险", ev.risks)
    skills = ev.skill_evaluations or []
    if skills:
        story.append(Paragraph("能力评价", styles["h2"]))
        rows = [["能力", "评价", "依据"]] + [
            [_s(s.get("skill")), _s(s.get("level")), _s(s.get("evidence"))] for s in skills
        ]
        sk = Table(rows, colWidths=[40 * mm, 30 * mm, 90 * mm])
        sk.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), styles["font"]), ("GRID", (0, 0), (-1, -1), 0.5, "#ccc")]))
        story.append(sk)
    if ev.report_markdown:
        _pdf_section(story, styles, "报告正文", ev.report_markdown)
    doc.build(story)
    return _safe_filename(task.task_no or "report", "pdf"), buf.getvalue()


def _pdf_meta_block(task: RecruitmentTask, styles, kind: str = "regular", with_answers: bool = True) -> list:
    story = [Paragraph(_title_for(task, kind, with_answers), styles["title"])]
    rows = [[k, _s(v)] for k, v in _kv_rows(task)]
    meta = Table(rows, colWidths=[40 * mm, 120 * mm])
    meta.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), styles["font"]), ("GRID", (0, 0), (-1, -1), 0.5, "#ccc")]))
    story.append(meta)
    story.append(Spacer(1, 12))
    return story


def _pdf_section(story, styles, title: str, value: Any) -> None:
    if value in (None, "", []):
        return
    story.append(Paragraph(title, styles["h2"]))
    story.append(Paragraph(_s(value), styles["normal"]))
    story.append(Spacer(1, 6))


def _pdf_list(story, styles, title: str, items: Any) -> None:
    if not items:
        return
    story.append(Paragraph(title, styles["h2"]))
    if isinstance(items, str):
        story.append(Paragraph(_s(items), styles["normal"]))
    else:
        text = "；".join(_s(x) for x in items)
        story.append(Paragraph(text, styles["normal"]))
    story.append(Spacer(1, 6))


def _score(value) -> str:
    return "-" if value in (None, "") else str(value)


def _s(value: Any) -> str:
    """段落文本安全化：转字符串并替换易破坏 reportlab 的尖括号。"""
    if value is None:
        return ""
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")