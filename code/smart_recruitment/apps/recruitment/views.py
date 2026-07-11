from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import EvaluationForm, SubmissionForm, TaskCreateForm
from .models import AiJob, AuditEvent, DevelopmentTask, Evaluation, RecruitmentTask, RegularQuestionSet, TaskAnalysis


def log_event(task, user, event_type: str, message: str, metadata=None) -> None:
    AuditEvent.objects.create(task=task, actor=user if user.is_authenticated else None, event_type=event_type, message=message, metadata=metadata or {})


@login_required
def task_list(request):
    tasks = RecruitmentTask.objects.select_related("candidate", "position", "department", "technical_owner").all()
    return render(request, "recruitment/task_list.html", {"tasks": tasks})


@login_required
@transaction.atomic
def task_create(request):
    form = TaskCreateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        task = form.save(request.user)
        TaskAnalysis.objects.create(task=task)
        AiJob.objects.create(task=task, job_type="analyze_position_resume", input_json={"task_id": task.id})
        log_event(task, request.user, "task_created", "创建招聘评测任务并进入待分析")
        messages.success(request, "任务已创建，后台分析任务已进入队列。")
        return redirect("task-detail", pk=task.pk)
    return render(request, "recruitment/task_form.html", {"form": form})


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
    if request.method == "POST":
        if question_set:
            question_set.status = "confirmed"
            question_set.save(update_fields=["status", "updated_at"])
        task.regular_question_status = "confirmed"
        task.overall_status = "pending_delivery"
        task.save(update_fields=["regular_question_status", "overall_status", "updated_at"])
        log_event(task, request.user, "regular_questions_confirmed", "确认普通题目集")
        messages.success(request, "普通题已确认。")
        return redirect("development-task", pk=task.pk)
    return render(request, "recruitment/regular_questions.html", {"task": task, "question_set": question_set})


@login_required
@transaction.atomic
def development_task_view(request, pk):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    dev_task = task.development_tasks.first()
    if request.method == "POST":
        if dev_task:
            dev_task.status = "pending_send"
            dev_task.save(update_fields=["status", "updated_at"])
        task.development_task_status = "pending_send"
        task.overall_status = "pending_delivery"
        task.save(update_fields=["development_task_status", "overall_status", "updated_at"])
        log_event(task, request.user, "development_task_ready", "现场开发题进入待发送")
        messages.success(request, "现场开发题已进入待发送状态。")
        return redirect("task-report", pk=task.pk)
    return render(request, "recruitment/development_task.html", {"task": task, "dev_task": dev_task})


@login_required
@transaction.atomic
def report_view(request, pk):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    evaluation, _ = Evaluation.objects.get_or_create(task=task)
    form = EvaluationForm(request.POST or None, instance=evaluation)
    submission_form = SubmissionForm()
    if request.method == "POST" and form.is_valid():
        evaluation = form.save(commit=False)
        if evaluation.confirmed and not evaluation.confirmed_at:
            evaluation.confirmed_by = request.user
            evaluation.confirmed_at = timezone.now()
            task.overall_status = "completed"
        else:
            task.overall_status = "pending_report_confirmation"
        evaluation.save()
        task.save(update_fields=["overall_status", "updated_at"])
        log_event(task, request.user, "report_saved", "保存评测报告")
        messages.success(request, "评测报告已保存。")
        return redirect("task-report", pk=task.pk)
    return render(request, "recruitment/report.html", {"task": task, "evaluation": evaluation, "form": form, "submission_form": submission_form})


@login_required
@transaction.atomic
def enqueue_job(request, pk, job_type):
    task = get_object_or_404(RecruitmentTask, pk=pk)
    valid_types = {key for key, _ in AiJob.TYPE_CHOICES}
    if job_type not in valid_types:
        messages.error(request, "未知的后台任务类型。")
        return redirect("task-detail", pk=pk)
    AiJob.objects.create(task=task, job_type=job_type, input_json={"task_id": task.id})
    log_event(task, request.user, "ai_job_queued", f"加入后台任务：{job_type}")
    messages.success(request, "后台任务已加入队列。")
    return redirect("task-detail", pk=pk)

