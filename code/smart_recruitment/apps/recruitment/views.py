from datetime import timedelta
from decimal import Decimal
import random
from uuid import uuid4
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import CandidateConfirmForm, EvaluationForm, PositionTemplateForm, SubmissionForm, TaskCreateForm
from .models import AiJob, Attachment, AuditEvent, Department, DevelopmentTask, Evaluation, PositionTemplate, RecruitmentTask, Submission, TaskAnalysis
from services.analysis_workflow import build_analysis, generate_development_task, generate_regular_question_variant, generate_regular_questions
from services import export_document as export_service
from services.llm_client import LLMError, get_llm_client
from services.position_template_parser import TemplateParseError, extract_position_template, extract_template_file
from services.resume_parser import extract_pdf_text, summarize_resume_text
from services.scoring_workflow import _as_dict, apply_score, build_report, score_submission


def _profile_needs_reparse(resume) -> bool:
    """判断简历画像是否需要重新解析：无简历、或画像为空、或上次解析失败兜底。"""
    if not resume:
        return False
    profile = resume.parsed_profile or {}
    if not profile:
        return True
    # 上一次解析是本地兜底或失败后兜底，说明 LLM 没真正抽到结构化字段
    source = (profile.get("_ai") or {}).get("source", "")
    if source in {"local_fallback", "local_fallback_after_error"}:
        return True
    if profile.get("_ai_error"):
        return True
    if not profile.get("name"):
        return True
    return False


def _reparse_resume(task, llm) -> None:
    """重新解析简历文本与画像，写回 resume。无简历或无文件时静默跳过。"""
    resume = task.resume
    if not resume or not getattr(resume.attachment, "file", None):
        return
    try:
        text = extract_pdf_text(resume.attachment.file.path)
    except Exception:  # noqa: BLE001 - PDF 抽取失败不应打断分析流程
        return
    if text:
        resume.resume_text = text
    resume.parsed_profile = summarize_resume_text(text, llm)
    resume.parse_status = "success" if text else "failed"
    resume.parse_error = "" if text else "未能从PDF中抽取文本，可能是扫描件。"
    resume.parsed_at = timezone.now()
    resume.save(update_fields=["resume_text", "parsed_profile", "parse_status", "parse_error", "parsed_at", "updated_at"])


# 将简历解析返回的学历描述映射到 CandidateConfirmForm.EDUCATION_CHOICES 的 key
_EDUCATION_MAP = {
    "高中": "high_school", "中专": "high_school", "高中/中专": "high_school",
    "大专": "college", "专科": "college",
    "本科": "bachelor", "学士": "bachelor",
    "硕士": "master", "研究生": "master",
    "博士": "phd",
}


def _normalize_education(value: str) -> str:
    if not value:
        return ""
    value = str(value).strip()
    if value in _EDUCATION_MAP.values():
        return value
    for keyword, key in _EDUCATION_MAP.items():
        if keyword in value:
            return key
    return ""


def _profile_to_form_initial(profile: dict) -> dict:
    """把 Resume.parsed_profile 映射到 CandidateConfirmForm 的 initial。"""
    profile = profile or {}
    skills = profile.get("skills") or []
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.replace(",", "、").replace("，", "、").split("、") if s.strip()]
    return {
        "candidate_name": profile.get("name", ""),
        "work_years": profile.get("work_years"),
        "education": _normalize_education(profile.get("education", "")),
        "current_company": profile.get("current_company", ""),
        "current_position": profile.get("current_position", ""),
        "candidate_email": profile.get("email", ""),
        "candidate_mobile": profile.get("mobile", ""),
        "skills_text": "、".join(skills) if isinstance(skills, list) else "",
    }


def log_event(task, user, event_type: str, message: str, metadata=None) -> None:
    AuditEvent.objects.create(task=task, actor=user if user.is_authenticated else None, event_type=event_type, message=message, metadata=metadata or {})


# 各阶段允许的前置状态，用于状态机校验，返回 True 表示允许该操作
def _status_allowed(task, *allowed) -> bool:
    return task.overall_status in allowed


_QUESTION_REGENERATE_ALLOWED_STATUSES = {
    "pending_analysis",
    "pending_verification_confirmation",
    "pending_question_review",
    "pending_delivery",
    "candidate_in_progress",
    "pending_collection",
    "pending_scoring",
    "pending_report_confirmation",
}


def _ai_source_label(source: str) -> str:
    return {
        "llm": "大模型生成",
        "local_fallback": "本地兜底",
        "local_fallback_after_error": "模型失败后兜底",
    }.get(source or "", "未知")


def _regular_question_context(question_set, selected_index: int = 0) -> dict:
    questions = question_set.questions if question_set and isinstance(question_set.questions, list) else []
    selected_index = max(0, min(selected_index, len(questions) - 1)) if questions else 0
    selected_question = questions[selected_index] if questions else None
    question_items = [{"index": idx, "question": q} for idx, q in enumerate(questions)]
    basic_question_items = [item for item in question_items if _regular_question_kind(item["question"]) == "basic"]
    qa_question_items = [item for item in question_items if _regular_question_kind(item["question"]) == "qa"]
    for group_items in (basic_question_items, qa_question_items):
        for display_no, item in enumerate(group_items, start=1):
            item["display_no"] = display_no
    basic_questions = [item["question"] for item in basic_question_items]
    qa_questions = [item["question"] for item in qa_question_items]
    display_no_by_index = {item["index"]: item["display_no"] for item in [*basic_question_items, *qa_question_items]}
    selected_display_no = display_no_by_index.get(selected_index, selected_index + 1)
    questions_for_client = []
    for idx, q in enumerate(questions):
        item = dict(q)
        item["_display_no"] = display_no_by_index.get(idx, idx + 1)
        questions_for_client.append(item)
    coverage = {}
    for q in questions:
        skill = q.get("skill") or "未标注"
        coverage[skill] = coverage.get(skill, 0) + 1
    source = selected_question.get("_ai_source") if selected_question else ""
    if not source and questions:
        source = questions[0].get("_ai_source", "")
    ai_error = selected_question.get("_ai_error") if selected_question else ""
    if not ai_error:
        for q in questions:
            if q.get("_ai_error"):
                ai_error = q["_ai_error"]
                break
    return {
        "questions": questions,
        "questions_for_client": questions_for_client,
        "basic_questions": basic_questions,
        "qa_questions": qa_questions,
        "basic_question_items": basic_question_items,
        "qa_question_items": qa_question_items,
        "selected_question": selected_question,
        "selected_index": selected_index,
        "selected_display_no": selected_display_no,
        "next_question": questions[selected_index + 1] if selected_index + 1 < len(questions) else None,
        "coverage": coverage,
        "question_source": source,
        "question_ai_error": ai_error,
    }


def _regular_question_kind(question: dict) -> str:
    q_type = str(question.get("type", "")).lower()
    if q_type in {"basic", "basic_question", "basic_skill", "skill", "基础技能验证"}:
        return "basic"
    return "qa"


def _shuffle_regular_questions(question_set) -> None:
    questions = question_set.questions if question_set and isinstance(question_set.questions, list) else []
    basic = [q for q in questions if _regular_question_kind(q) == "basic"]
    qa = [q for q in questions if _regular_question_kind(q) == "qa"]
    random.shuffle(basic)
    random.shuffle(qa)
    question_set.questions = [*basic, *qa]


@login_required
def task_list(request):
    tasks = RecruitmentTask.objects.select_related("candidate", "position", "department", "technical_owner").all()
    week_ago = timezone.now() - timedelta(days=7)
    metrics = {
        "in_progress": tasks.exclude(overall_status__in=["completed", "cancelled"]).count(),
        "pending_review": tasks.filter(regular_question_status__in=["generated", "reviewing"]).count(),
        "pending_collection": tasks.filter(overall_status__in=["pending_delivery", "candidate_in_progress", "pending_collection"]).count(),
        "completed_this_week": tasks.filter(overall_status="completed", updated_at__gte=week_ago).count(),
    }
    return render(request, "recruitment/task_list.html", {"tasks": tasks, "metrics": metrics})


@login_required
@transaction.atomic
def task_create(request):
    initial = {}
    template_id = request.GET.get("template")
    if template_id:
        template = PositionTemplate.objects.filter(pk=template_id).select_related("department").first()
        if template:
            initial = {
                "position_template": template,
                "position_name": template.name,
                "department_name": template.department.name if template.department else "",
                "job_level": template.job_level,
                "raw_job_description": template.raw_job_description,
            }
    form = TaskCreateForm(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        task = form.save(request.user)
        log_event(task, request.user, "task_created", "创建招聘评测任务（草稿），等待候选人信息确认")
        # 有简历则入队结构化解析并跳转过渡页；无简历直接进入候选人确认手填
        if task.resume:
            AiJob.objects.create(task=task, job_type="parse_resume", input_json={"task_id": task.id})
            return redirect("task-parsing", pk=task.pk)
        messages.info(request, "未上传简历，请手动填写候选人信息。")
        return redirect("candidate-confirm", pk=task.pk)
    return render(request, "recruitment/task_form.html", {"form": form})


@login_required
def position_template_json(request, pk):
    """供新建任务页选择岗位模板后即时回填岗位字段。"""
    template = get_object_or_404(PositionTemplate.objects.select_related("department"), pk=pk, status="published")
    return JsonResponse({
        "name": template.name,
        "department_name": template.department.name if template.department else "",
        "job_level": template.job_level,
        "raw_job_description": template.raw_job_description,
    })


@login_required
def task_parsing(request, pk):
    """第1步提交后的过渡页：展示简历解析进度，前端轮询状态接口。"""
    task = get_object_or_404(RecruitmentTask, pk=pk)
    # 未上传简历则无需解析，直接进入候选人确认
    if not task.resume:
        return redirect("candidate-confirm", pk=task.pk)
    return render(request, "recruitment/parsing.html", {"task": task})


@login_required
def task_parsing_status(request, pk):
    """返回当前任务 parse_resume 后台任务的状态，供过渡页轮询。"""
    task = get_object_or_404(RecruitmentTask, pk=pk)
    if not task.resume:
        return JsonResponse({"status": "skipped", "redirect": "candidate-confirm", "progress": 100})
    job = task.ai_jobs.filter(job_type="parse_resume").order_by("-created_at").first()
    if not job:
        return JsonResponse({"status": "queued", "progress": 0})
    payload = {"status": job.status, "progress": job.progress}
    if job.status == "success":
        payload["redirect"] = "candidate-confirm"
        payload["parse_status"] = task.resume.parse_status
    elif job.status == "failed":
        payload["redirect"] = "candidate-confirm"
        payload["error"] = job.error_message or "解析失败"
    return JsonResponse(payload)


@login_required
@transaction.atomic
def candidate_confirm(request, pk):
    """第2步：候选人信息确认页。展示 LLM 解析回填结果，用户核对/手填后确认建任务。"""
    task = get_object_or_404(RecruitmentTask.objects.select_related("candidate", "position", "resume"), pk=pk)
    # 已确认过的任务不允许再回到此页
    if task.overall_status not in ("draft",):
        messages.warning(request, "该任务的候选人信息已确认，无需再次确认。")
        return redirect("task-detail", pk=task.pk)
    resume = task.resume
    profile = resume.parsed_profile if (resume and resume.parse_status == "success") else {}
    parsed_ok = bool(profile and profile.get("name"))
    if request.method == "POST":
        form = CandidateConfirmForm(request.POST)
        if form.is_valid():
            form.save(task, request.user)
            TaskAnalysis.objects.create(task=task)
            task.overall_status = "pending_analysis"
            task.save(update_fields=["overall_status", "updated_at"])
            AiJob.objects.create(task=task, job_type="analyze_position_resume", input_json={"task_id": task.id})
            log_event(task, request.user, "candidate_confirmed", "确认候选人信息并进入岗位简历分析")
            messages.success(request, "候选人信息已确认，后台分析任务已进入队列。")
            return redirect("task-detail", pk=task.pk)
    else:
        form = CandidateConfirmForm(initial=_profile_to_form_initial(profile))
    return render(request, "recruitment/candidate_confirm.html", {
        "task": task,
        "form": form,
        "parsed_ok": parsed_ok,
        "has_resume": bool(resume),
        "parse_failed": bool(resume and resume.parse_status == "failed"),
    })


def _log_template_event(template, user, event_type: str, message: str, metadata=None) -> None:
    """记录岗位模板变更审计事件。task 留空，metadata 中带 template_id。"""
    AuditEvent.objects.create(
        task=None,
        actor=user if user.is_authenticated else None,
        event_type=event_type,
        message=message,
        metadata={"template_id": template.pk, "template_name": template.name, **(metadata or {})},
    )


@login_required
@transaction.atomic
def position_template_list(request, pk=None):
    query = request.GET.get("q", "").strip()
    templates = PositionTemplate.objects.select_related("department").all()
    if query:
        templates = templates.filter(name__icontains=query)

    selected = None
    if pk:
        selected = get_object_or_404(PositionTemplate.objects.select_related("department"), pk=pk)
    elif templates.exists():
        selected = templates.first()

    if request.method == "POST":
        if request.POST.get("action") == "create":
            template = PositionTemplate.objects.create(name="新岗位模板", created_by=request.user)
            _log_template_event(template, request.user, "template_created", "新建岗位模板")
            messages.success(request, "已创建新岗位模板。")
            return redirect("position-template-detail", pk=template.pk)

        if request.POST.get("action") == "upload_parse":
            upload = request.FILES.get("template_file")
            if not upload:
                messages.error(request, "请先选择 Word 或 Excel 文件。")
                return redirect("position-template-detail", pk=selected.pk) if selected else redirect("position-template-list")
            try:
                filename, text = extract_template_file(upload)
                if not text:
                    raise TemplateParseError("未能从文件中解析到文本内容。")
                payload = extract_position_template(text, get_llm_client())
            except TemplateParseError as exc:
                messages.error(request, str(exc))
                return redirect("position-template-detail", pk=selected.pk) if selected else redirect("position-template-list")

            department = None
            department_name = payload.get("department_name", "").strip()
            if department_name:
                department = Department.objects.filter(name=department_name).first()
                if not department:
                    department = Department.objects.create(name=department_name, code=f"DEPT-{uuid4().hex[:12].upper()}")
            # P0 修复：上传解析始终新建模板，不覆盖已有模板，避免数据丢失
            template = PositionTemplate(created_by=request.user)
            apply_position_template_payload(template, payload, department)
            template.status = "draft"
            template.save()
            _log_template_event(template, request.user, "template_parsed", f"上传解析 {filename} 生成岗位模板", {"filename": filename, "ai_source": (payload.get("_ai") or {}).get("source")})
            messages.success(request, f"已解析 {filename} 并创建新岗位模板，请核对后保存或发布。")
            return redirect("position-template-detail", pk=template.pk)

        if request.POST.get("action") == "disable":
            template = get_object_or_404(PositionTemplate, pk=pk)
            template.status = "disabled"
            template.save(update_fields=["status", "updated_at"])
            _log_template_event(template, request.user, "template_disabled", "岗位模板已停用")
            messages.success(request, "岗位模板已停用。")
            return redirect("position-template-detail", pk=template.pk)

        if request.POST.get("action") == "enable":
            template = get_object_or_404(PositionTemplate, pk=pk)
            # 停用 -> 草稿；如需发布走发布按钮
            template.status = "draft"
            template.save(update_fields=["status", "updated_at"])
            _log_template_event(template, request.user, "template_enabled", "岗位模板已重新启用")
            messages.success(request, "岗位模板已重新启用为草稿，可再次编辑/发布。")
            return redirect("position-template-detail", pk=template.pk)

        if request.POST.get("action") == "delete":
            template = get_object_or_404(PositionTemplate, pk=pk)
            template_name = template.name
            _log_template_event(template, request.user, "template_deleted", "删除岗位模板")
            template.delete()
            messages.success(request, f"岗位模板“{template_name}”已删除。")
            return redirect("position-template-list")

        selected = get_object_or_404(PositionTemplate, pk=pk)
        form = PositionTemplateForm(request.POST, instance=selected)
        if form.is_valid():
            template = form.save(commit=False)
            if not template.created_by:
                template.created_by = request.user
            if request.POST.get("action") == "publish":
                template.status = "published"
                template.published_by = request.user
                template.published_at = timezone.now()
                message = "岗位模板已发布。"
                _log_template_event(template, request.user, "template_published", "发布岗位模板")
            else:
                # P0 修复：保存草稿不覆盖已发布/停用状态，只对草稿态模板保持草稿
                was_published = selected.status == "published" if selected and selected.pk else False
                was_disabled = selected.status == "disabled" if selected and selected.pk else False
                if was_published:
                    # 已发布模板字段有改动后仍保持已发布（视为修订）
                    message = "已发布模板已更新。"
                    _log_template_event(template, request.user, "template_updated", "更新已发布岗位模板")
                elif was_disabled:
                    message = "停用模板已更新（保持停用状态，需重新启用后再发布）。"
                    template.status = "disabled"
                    _log_template_event(template, request.user, "template_updated", "更新停用岗位模板")
                else:
                    template.status = "draft"
                    message = "岗位模板草稿已保存。"
                    _log_template_event(template, request.user, "template_updated", "保存岗位模板草稿")
            template.save()
            messages.success(request, message)
            return redirect("position-template-detail", pk=template.pk)
    else:
        form = PositionTemplateForm(instance=selected) if selected else None

    return render(
        request,
        "recruitment/position_templates.html",
        {"templates": templates, "selected": selected, "form": form, "query": query},
    )


def apply_position_template_payload(template: PositionTemplate, payload: dict, department: Department | None) -> None:
    template.name = payload.get("name") or template.name or "未命名岗位模板"
    if department:
        template.department = department
    template.job_level = payload.get("job_level") or template.job_level or "middle"
    template.scenario = payload.get("scenario") or template.scenario or "社会招聘"
    template.description = payload.get("description") or template.description
    template.responsibilities = payload.get("responsibilities") or template.responsibilities or []
    template.requirements = payload.get("requirements") or template.requirements or []
    template.technical_tags = payload.get("technical_tags") or template.technical_tags or []
    template.keywords = payload.get("keywords") or template.keywords or []


@login_required
def task_detail(request, pk):
    task = get_object_or_404(
        RecruitmentTask.objects.select_related("candidate", "position", "department", "hr_owner", "technical_owner"),
        pk=pk,
    )
    return render(request, "recruitment/task_detail.html", {"task": task})


@login_required
@transaction.atomic
def analysis_view(request, pk):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    analysis, _ = TaskAnalysis.objects.get_or_create(task=task)
    if request.method == "POST":
        if not _status_allowed(task, "pending_verification_confirmation", "pending_question_review"):
            messages.error(request, "当前阶段无法确认验证项。")
            return redirect("task-analysis", pk=task.pk)
        analysis.confirmed = True
        analysis.confirmed_by = request.user
        analysis.confirmed_at = timezone.now()
        analysis.save(update_fields=["confirmed", "confirmed_by", "confirmed_at", "updated_at"])
        task.overall_status = "pending_question_review"
        task.regular_question_status = "generated" if task.regular_question_sets.exists() else "not_generated"
        task.save(update_fields=["overall_status", "regular_question_status", "updated_at"])
        log_event(task, request.user, "analysis_confirmed", "确认待验证能力项")
        messages.success(request, "验证项已确认，可以进入普通题审核或开发题配置。")
        return redirect("regular-questions", pk=task.pk)
    return render(request, "recruitment/analysis.html", {"task": task, "analysis": analysis})


@login_required
@transaction.atomic
def regular_questions_view(request, pk):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    question_set = task.regular_question_sets.first()
    selected_index = _safe_int(request.GET.get("q"), default=0)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "regenerate_set":
            if not _status_allowed(task, *_QUESTION_REGENERATE_ALLOWED_STATUSES):
                log_event(
                    task,
                    request.user,
                    "regular_questions_regenerate_blocked",
                    "重新生成普通题被状态机拦截",
                    {"overall_status": task.overall_status},
                )
                messages.error(request, "当前阶段无法重新生成普通题。")
                return redirect("regular-questions", pk=task.pk)
            question_set = generate_regular_questions(task, get_llm_client())
            _shuffle_regular_questions(question_set)
            question_set.save(update_fields=["questions", "updated_at"])
            question_source = question_set.questions[0].get("_ai_source", "") if question_set.questions else ""
            task.regular_question_status = "generated"
            task.overall_status = "pending_question_review"
            task.save(update_fields=["regular_question_status", "overall_status", "updated_at"])
            log_event(
                task,
                request.user,
                "regular_questions_regenerated",
                "同步重新生成整套普通题并打乱题序",
                {"question_set_id": question_set.id, "version": question_set.version, "source": question_source},
            )
            messages.success(request, f"普通题已重新生成：版本 v{question_set.version}，来源 {_ai_source_label(question_source)}。")
            return redirect("regular-questions", pk=task.pk)
        if action in {"approve", "simplify", "increase", "replace", "delete"}:
            if not question_set or not isinstance(question_set.questions, list) or not question_set.questions:
                messages.error(request, "还没有可操作的普通题，请先生成题目。")
                return redirect("regular-questions", pk=task.pk)
            question_index = _safe_int(request.POST.get("question_index"), default=selected_index)
            questions = list(question_set.questions)
            if question_index < 0 or question_index >= len(questions):
                messages.error(request, "题目序号无效。")
                return redirect("regular-questions", pk=task.pk)
            if action == "approve":
                questions[question_index]["status"] = "confirmed"
                message = "题目已通过。"
            elif action == "delete":
                questions.pop(question_index)
                selected_index = max(0, min(question_index, len(questions) - 1)) if questions else 0
                message = "题目已删除。"
            else:
                questions[question_index] = generate_regular_question_variant(task, questions[question_index], action, get_llm_client())
                message = {"simplify": "题目已简化。", "increase": "题目难度已提高。", "replace": "已换一道同能力题。"}[action]
            question_set.questions = questions
            question_set.status = "reviewing"
            question_set.save(update_fields=["questions", "status", "updated_at"])
            log_event(task, request.user, f"regular_question_{action}", message, {"question_index": question_index})
            messages.success(request, message)
            return redirect(f"{request.path}?q={selected_index}")
        if action != "confirm":
            messages.error(request, "未知的题目操作。")
            return redirect("regular-questions", pk=task.pk)
        if not _status_allowed(task, "pending_question_review", "pending_verification_confirmation", "pending_analysis"):
            messages.error(request, "当前阶段无法确认普通题。")
            return redirect("regular-questions", pk=task.pk)
        if not question_set or not question_set.questions:
            messages.error(request, "还没有可确认的普通题，请先生成题目。")
            return redirect("regular-questions", pk=task.pk)
        if question_set:
            question_set.status = "confirmed"
            question_set.save(update_fields=["status", "updated_at"])
        if not task.development_tasks.exists():
            generate_development_task(task, get_llm_client())
        task.regular_question_status = "confirmed"
        task.overall_status = "pending_delivery"
        task.development_task_status = "reviewing"
        task.save(update_fields=["regular_question_status", "development_task_status", "overall_status", "updated_at"])
        log_event(task, request.user, "regular_questions_confirmed", "确认普通题目集")
        messages.success(request, "普通题已确认。")
        return redirect("development-task", pk=task.pk)
    context = {"task": task, "question_set": question_set}
    context.update(_regular_question_context(question_set, selected_index))
    return render(request, "recruitment/regular_questions.html", context)


@login_required
@transaction.atomic
def development_task_view(request, pk):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    dev_task = task.development_tasks.order_by("-version").first()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "regenerate":
            if not _status_allowed(task, *_QUESTION_REGENERATE_ALLOWED_STATUSES):
                log_event(
                    task,
                    request.user,
                    "development_task_regenerate_blocked",
                    "重新生成现场开发题被状态机拦截",
                    {"overall_status": task.overall_status},
                )
                messages.error(request, "当前阶段无法重新生成现场开发题。")
                return redirect("development-task", pk=task.pk)
            direction = (request.POST.get("direction_custom") or request.POST.get("direction") or "").strip()
            dev_task = generate_development_task(task, get_llm_client(), direction=direction)
            task.development_task_status = "reviewing"
            if task.overall_status in {"pending_analysis", "pending_verification_confirmation"}:
                task.overall_status = "pending_question_review"
            task.save(update_fields=["development_task_status", "overall_status", "updated_at"])
            dev_source = (dev_task.content.get("_ai") or {}).get("source", "")
            log_event(
                task,
                request.user,
                "development_task_regenerated",
                "同步按方向重新生成现场开发题",
                {"direction": direction, "development_task_id": dev_task.id, "version": dev_task.version, "source": dev_source},
            )
            messages.success(request, f"现场开发题已重新生成：版本 v{dev_task.version}，来源 {_ai_source_label(dev_source)}。")
            return redirect("development-task", pk=task.pk)

        if action != "send":
            messages.error(request, "未知的现场开发题操作。")
            return redirect("development-task", pk=task.pk)

        if not _status_allowed(task, "pending_delivery", "pending_question_review"):
            messages.error(request, "当前阶段无法发送开发题。")
            return redirect("development-task", pk=task.pk)
        if not dev_task:
            messages.error(request, "还没有可发送的现场开发题，请先生成题目。")
            return redirect("development-task", pk=task.pk)
        if dev_task:
            dev_task.status = "sent"
            if not dev_task.sent_at:
                dev_task.sent_at = timezone.now()
            dev_task.save(update_fields=["status", "sent_at", "updated_at"])
        task.development_task_status = "sent"
        task.overall_status = "pending_delivery"
        task.save(update_fields=["development_task_status", "overall_status", "updated_at"])
        log_event(task, request.user, "development_task_ready", "现场开发题已发送给候选人")
        messages.success(request, "现场开发题已发送给候选人。")
        return redirect("task-report", pk=task.pk)
    delivery_events = task.events.filter(event_type__in=["development_task_ready", "development_task_regenerated"]).order_by("-created_at")[:8]
    return render(request, "recruitment/development_task.html", {"task": task, "dev_task": dev_task, "delivery_events": delivery_events})


@login_required
@transaction.atomic
def report_view(request, pk):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    evaluation, _ = Evaluation.objects.get_or_create(task=task)
    form = EvaluationForm(request.POST or None, instance=evaluation)
    submission_form = SubmissionForm()
    question_set = task.regular_question_sets.order_by("-version").first()
    dev_task = task.development_tasks.order_by("-version").first()
    if request.method == "POST" and form.is_valid():
        evaluation = form.save(commit=False)
        action = request.POST.get("action")

        # 收集逐题人工评分，写回 evaluation.ai_suggestion 下的 item_scores
        item_scores = _collect_item_scores(request.POST, regular_question_set=question_set, dev_task=dev_task)
        suggestions = _as_dict(evaluation.ai_suggestion)
        if item_scores["regular"]:
            suggestions["regular_item_scores"] = item_scores["regular"]
        if item_scores["development"]:
            suggestions["development_item_scores"] = item_scores["development"]
        evaluation.ai_suggestion = suggestions

        if action == "confirm_report":
            if not _status_allowed(task, "pending_report_confirmation", "pending_scoring", "completed"):
                messages.error(request, "当前阶段无法确认报告，请先完成评分。")
                return redirect("task-report", pk=task.pk)
            evaluation.confirmed = True
            if not evaluation.confirmed_at:
                evaluation.confirmed_by = request.user
                evaluation.confirmed_at = timezone.now()
            task.overall_status = "completed"
        else:
            # 仅保存：若报告已确认，不得回退；否则进入待确认报告
            if task.overall_status == "completed":
                pass  # 保持已完成，不撤销已确认结论
            else:
                task.overall_status = "pending_report_confirmation"
        evaluation.save()
        task.save(update_fields=["overall_status", "updated_at"])
        log_event(task, request.user, "report_saved", "保存评测报告")
        messages.success(request, "评测报告已保存。")
        return redirect("task-report", pk=task.pk)
    return render(
        request,
        "recruitment/report.html",
        {
            "task": task,
            "evaluation": evaluation,
            "form": form,
            "submission_form": submission_form,
            "question_set": question_set,
            "dev_task": dev_task,
        },
    )


def _collect_item_scores(post_data, regular_question_set=None, dev_task=None) -> dict:
    """从 POST 中收集人工逐题评分；输入名形如 regular_score_0 / development_score_0。"""
    regular = {}
    development = {}
    if regular_question_set and isinstance(regular_question_set.questions, list):
        for idx in range(len(regular_question_set.questions)):
            value = post_data.get(f"regular_score_{idx}")
            if value not in (None, ""):
                regular[str(idx)] = _safe_score(value)
    if dev_task:
        criteria = (dev_task.content or {}).get("acceptance_criteria") if isinstance(dev_task.content, dict) else None
        count = len(criteria) if isinstance(criteria, list) else 0
        for idx in range(count):
            value = post_data.get(f"development_score_{idx}")
            if value not in (None, ""):
                development[str(idx)] = _safe_score(value)
    return {"regular": regular, "development": development}


def _safe_score(value) -> float:
    try:
        score = float(value)
        return max(0.0, min(100.0, score))
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@login_required
@transaction.atomic
def upload_submission(request, pk):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    if request.method != "POST":
        return redirect("task-report", pk=pk)

    submission_type = request.POST.get("submission_type")
    if submission_type not in {"regular", "development"}:
        messages.error(request, "提交类型不正确。")
        return redirect("task-report", pk=pk)

    if not _status_allowed(task, "pending_delivery", "pending_collection", "candidate_in_progress", "pending_scoring"):
        messages.error(request, "当前阶段无法上传提交结果。")
        return redirect("task-report", pk=pk)

    submission = Submission.objects.create(
        task=task,
        submission_type=submission_type,
        integrity_status=request.POST.get("integrity_status") or "unchecked",
        notes=request.POST.get("notes", ""),
    )
    for upload in request.FILES.getlist("files"):
        attachment = Attachment.objects.create(
            file=upload,
            original_name=upload.name,
            purpose="regular_submission" if submission_type == "regular" else "development_submission",
            mime_type=getattr(upload, "content_type", "") or "",
            file_size=getattr(upload, "size", 0) or 0,
            uploaded_by=request.user,
        )
        submission.attachments.add(attachment)

    if submission_type == "regular":
        task.regular_question_status = "collected"
    else:
        task.development_task_status = "collected"
        dev_task = task.development_tasks.order_by("-version").first()
        if dev_task and not dev_task.collected_at:
            dev_task.status = "collected"
            dev_task.collected_at = timezone.now()
            dev_task.save(update_fields=["status", "collected_at", "updated_at"])
    task.overall_status = "pending_scoring"
    task.save(update_fields=["regular_question_status", "development_task_status", "overall_status", "updated_at"])
    log_event(task, request.user, "submission_uploaded", f"上传{submission.get_submission_type_display()}提交结果")
    messages.success(request, "候选人提交结果已上传，可以发起AI辅助评分。")
    return redirect("task-report", pk=pk)


@login_required
@transaction.atomic
def enqueue_job(request, pk, job_type):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    valid_types = {key for key, _ in AiJob.TYPE_CHOICES}

    # 仅允许 POST，避免 GET 链接触发副作用任务
    if request.method != "POST":
        messages.error(request, "请通过表单提交后台任务，不要直接访问链接。")
        return redirect("task-detail", pk=pk)
    # 回跳目标：优先 Referer，缺省回任务总览；务必用 redirect() 包成 HttpResponseRedirect，
    # 不能直接返回字符串，否则响应中间件（如 clickjacking）会对 str 调用 .get() 报错。
    redirect_to = request.META.get("HTTP_REFERER") or ""
    fallback = redirect("task-detail", pk=pk)
    if job_type not in valid_types:
        messages.error(request, "未知的后台任务类型。")
        return redirect(redirect_to) if redirect_to else fallback

    # 去重：同任务已存在 queued/running 的同类型任务时不再入队
    pending = AiJob.objects.filter(task=task, job_type=job_type, status__in=["queued", "running"])
    if pending.exists():
        messages.warning(request, "该后台任务已在队列中或正在运行，无需重复加入。")
        return redirect(redirect_to) if redirect_to else fallback

    AiJob.objects.create(task=task, job_type=job_type, input_json={"task_id": task.id})
    log_event(task, request.user, "ai_job_queued", f"加入后台任务：{job_type}")
    messages.success(request, "后台任务已加入队列，处理完成后刷新本页可查看最新结果。")
    return redirect(redirect_to) if redirect_to else fallback


# ---------------------------------------------------------------------------
# 同步执行 AI 动作（不排队，HTTP 请求内直调 LLM 工作流后立即跳回页面）
# ---------------------------------------------------------------------------

_AI_ACTION_LABELS = {
    "score_regular_submission": "普通题AI评分",
    "score_development_submission": "开发题AI评分",
    "generate_report": "生成报告草稿",
    "analyze_position_resume": "重新解析",
}


@login_required
@transaction.atomic
def run_ai_action_now(request, pk, job_type):
    """报告页/任务页上的 AI 动作改为同步执行：直接调用 LLM 工作流写库，跳过 AiJob 队列。

    与 enqueue_job + run_worker 相比，这里不创建 AiJob、不排队，请求期间在浏览器
    前端用 js-wait-mask 给出"处理中"反馈；LLM 调用通常 10~30s 内完成即跳回页面。
    """
    task = get_object_or_404(RecruitmentTask, pk=pk)
    label = _AI_ACTION_LABELS.get(job_type)
    fallback_url = "task-detail" if job_type == "analyze_position_resume" else "task-report"
    redirect_to = request.META.get("HTTP_REFERER") or ""

    if request.method != "POST":
        messages.error(request, "请通过页面按钮提交，不要直接访问链接。")
        return redirect(redirect_to) if redirect_to else redirect(fallback_url, pk=pk)
    if not label:
        messages.error(request, "未知的 AI 动作类型。")
        return redirect(redirect_to) if redirect_to else redirect(fallback_url, pk=pk)

    try:
        llm = get_llm_client()
        if job_type == "analyze_position_resume":
            # 重新解析前：若简历画像为空或上次解析失败兜底，先把简历重抽一遍，
            # 否则 build_analysis 拿不到 name/skills 等结构化字段，候选人确认页就回填不出信息。
            if _profile_needs_reparse(task.resume):
                _reparse_resume(task, llm)
            # 与 run_worker 的 analyze_position_resume 分支保持一致
            analysis_payload = build_analysis(task, llm)
            analysis = task.analysis
            for field, value in analysis_payload.items():
                setattr(analysis, field, value)
            analysis.save()
            if not task.regular_question_sets.exists():
                generate_regular_questions(task, llm)
            if task.development_task_status != "not_enabled" and not task.development_tasks.exists():
                generate_development_task(task, llm)
            task.overall_status = "pending_verification_confirmation"
            task.regular_question_status = "generated"
            if task.development_task_status == "pending_generation":
                task.development_task_status = "reviewing"
            task.save(update_fields=["overall_status", "regular_question_status", "development_task_status", "updated_at"])
        elif job_type == "score_regular_submission":
            payload = score_submission(task, "regular", llm)
            apply_score(task, "regular", payload)
            task.regular_question_status = "scored"
            task.overall_status = "pending_scoring" if task.development_task_status not in {"scored", "not_enabled"} else "pending_report_confirmation"
            task.save(update_fields=["regular_question_status", "overall_status", "updated_at"])
        elif job_type == "score_development_submission":
            payload = score_submission(task, "development", llm)
            apply_score(task, "development", payload)
            task.development_task_status = "scored"
            task.overall_status = "pending_report_confirmation" if task.regular_question_status == "scored" else "pending_scoring"
            task.save(update_fields=["development_task_status", "overall_status", "updated_at"])
        elif job_type == "generate_report":
            payload = build_report(task, llm)
            evaluation, _ = Evaluation.objects.get_or_create(task=task)
            evaluation.ai_suggestion = payload["ai_suggestion"]
            evaluation.skill_evaluations = payload["skill_evaluations"]
            evaluation.strengths = payload.get("strengths", evaluation.strengths)
            evaluation.risks = payload.get("risks", evaluation.risks)
            evaluation.recommendation = payload.get("recommendation", evaluation.recommendation)
            evaluation.report_markdown = payload["report_markdown"]
            evaluation.save()
            task.overall_status = "pending_report_confirmation"
            task.save(update_fields=["overall_status", "updated_at"])
    except LLMError as exc:
        log_event(task, request.user, "ai_action_sync_failed", f"{label}失败（模型）", {"job_type": job_type, "error": str(exc)[:500]})
        messages.error(request, f"{label}失败：{exc}")
        return redirect(redirect_to) if redirect_to else redirect(fallback_url, pk=task.pk)
    except Exception as exc:  # noqa: BLE001 - 同步路径需兜底避免 500 打断页面
        log_event(task, request.user, "ai_action_sync_failed", f"{label}失败", {"job_type": job_type, "error": str(exc)[:500]})
        messages.error(request, f"{label}失败：{exc}")
        return redirect(redirect_to) if redirect_to else redirect(fallback_url, pk=task.pk)

    log_event(task, request.user, "ai_action_sync", f"同步执行{label}", {"job_type": job_type})
    messages.success(request, f"{label}已完成。")
    return redirect(redirect_to) if redirect_to else redirect(fallback_url, pk=task.pk)


# ---------------------------------------------------------------------------
# 文档导出（同步直下载，不走 AiJob 队列）
# ---------------------------------------------------------------------------

_CONTENT_TYPE = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf"}
_VALID_FORMATS = {"docx", "pdf"}


def _build_export_response(task, kind: str, fmt: str, with_answers: bool = True) -> HttpResponse:
    """根据 kind/fmt 调用对应服务函数生成文件并返回下载响应。"""
    builder = {
        ("regular", "docx"): export_service.build_regular_questions_docx,
        ("regular", "pdf"): export_service.build_regular_questions_pdf,
        ("development", "docx"): export_service.build_development_task_docx,
        ("development", "pdf"): export_service.build_development_task_pdf,
        ("report", "docx"): export_service.build_report_docx,
        ("report", "pdf"): export_service.build_report_pdf,
    }[(kind, fmt)]
    filename, content = builder(task, with_answers=with_answers) if kind == "regular" else builder(task)
    response = HttpResponse(content, content_type=_CONTENT_TYPE[fmt])
    # 同时提供 ASCII 与 UTF-8 文件名，兼容性最好
    response["Content-Disposition"] = f"attachment; filename={quote(filename)}"
    response["Content-Length"] = str(len(content))
    return response


@login_required
def export_regular_questions(request, pk, fmt):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    if fmt not in _VALID_FORMATS:
        messages.error(request, "仅支持 docx 或 pdf 格式。")
        return redirect("regular-questions", pk=pk)
    with_answers = request.GET.get("with_answers") in ("1", "true", "True")
    log_event(task, request.user, "export_regular", f"导出普通题（部门版={with_answers}，{fmt}）")
    return _build_export_response(task, "regular", fmt, with_answers=with_answers)


@login_required
def export_development_task(request, pk, fmt):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    if fmt not in _VALID_FORMATS:
        messages.error(request, "仅支持 docx 或 pdf 格式。")
        return redirect("development-task", pk=pk)
    log_event(task, request.user, "export_development", f"导出现场开发题（{fmt}）")
    return _build_export_response(task, "development", fmt)


@login_required
def export_report(request, pk, fmt):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    if fmt not in _VALID_FORMATS:
        messages.error(request, "仅支持 docx 或 pdf 格式。")
        return redirect("task-report", pk=pk)
    log_event(task, request.user, "export_report", f"导出评测报告（{fmt}）")
    return _build_export_response(task, "report", fmt)
