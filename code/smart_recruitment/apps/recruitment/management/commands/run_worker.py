import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.utils import OperationalError
from django.utils import timezone

from apps.recruitment.models import AiJob, Evaluation
from services.candidate_sync import sync_candidate_from_profile
from services.analysis_workflow import build_analysis, generate_development_task, generate_regular_questions
from services.llm_client import get_llm_client
from services.resume_parser import extract_pdf_text, summarize_resume_text
from services.scoring_workflow import apply_score, build_report, score_submission


def _profile_needs_reparse(resume) -> bool:
    """简历画像是否需要重解析：空画像、或上次失败兜底、或无姓名。"""
    if not resume:
        return False
    profile = resume.parsed_profile or {}
    if not profile:
        return True
    source = (profile.get("_ai") or {}).get("source", "")
    if source in {"local_fallback", "local_fallback_after_error"}:
        return True
    if profile.get("_ai_error"):
        return True
    if not profile.get("name"):
        return True
    return False


def _reparse_resume(task, llm) -> None:
    """重新抽取简历文本与结构化画像，写回 resume。无文件时静默跳过。"""
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
    sync_candidate_from_profile(task, resume.parsed_profile)


class Command(BaseCommand):
    help = "运行轻量后台Worker，轮询 ai_job 表并处理队列任务。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="只处理一个任务后退出")
        parser.add_argument("--sleep", type=float, default=2.0, help="没有任务时的等待秒数")
        parser.add_argument("--stale-minutes", type=int, default=10, help="将运行超过指定分钟数的旧任务标记为失败")

    def handle(self, *args, **options):
        self.llm = get_llm_client()
        if self.llm:
            self.stdout.write(f"已启用大模型：{self.llm.model} @ {self.llm.base_url}")
        else:
            self.stdout.write("未配置 LLM_API_KEY，将使用本地兜底工作流。")
        while True:
            self.recover_stale_jobs(options["stale_minutes"])
            job = self.claim_job()
            if not job:
                if options["once"]:
                    self.stdout.write("没有待处理任务。")
                    return
                time.sleep(options["sleep"])
                continue
            self.execute_job(job)
            if options["once"]:
                return

    @transaction.atomic
    def claim_job(self):
        job = AiJob.objects.select_for_update().filter(status="queued").order_by("created_at").first()
        if not job:
            return None
        job.status = "running"
        job.progress = 10
        job.started_at = timezone.now()
        job.save(update_fields=["status", "progress", "started_at", "updated_at"])
        return job

    def recover_stale_jobs(self, stale_minutes: int):
        cutoff = timezone.now() - timedelta(minutes=stale_minutes)
        stale_jobs = AiJob.objects.filter(status="running", started_at__lt=cutoff)
        count = stale_jobs.update(
            status="failed",
            error_message=f"后台任务运行超过 {stale_minutes} 分钟，已自动标记为失败，请重新触发。",
            finished_at=timezone.now(),
            updated_at=timezone.now(),
        )
        if count:
            self.stderr.write(self.style.WARNING(f"已恢复 {count} 个超时运行任务。"))

    def execute_job(self, job):
        try:
            task = job.task
            result = {}
            if job.job_type == "parse_resume":
                resume = task.resume
                if not resume:
                    result = {"skipped": "任务未关联简历"}
                else:
                    resume.parse_status = "processing"
                    resume.save(update_fields=["parse_status", "updated_at"])
                    job.progress = 30
                    job.save(update_fields=["progress", "updated_at"])
                    text = extract_pdf_text(resume.attachment.file.path)
                    resume.resume_text = text
                    job.progress = 60
                    job.save(update_fields=["progress", "updated_at"])
                    resume.parsed_profile = summarize_resume_text(text, self.llm)
                    resume.parse_status = "success" if text else "failed"
                    resume.parse_error = "" if text else "未能从PDF中抽取文本，可能是扫描件。"
                    resume.parsed_at = timezone.now()
                    resume.save()
                    sync_candidate_from_profile(task, resume.parsed_profile)
                    result = {"parse_status": resume.parse_status}
            elif job.job_type == "analyze_position_resume":
                # 第2步确认已建好 TaskAnalysis；此处负责岗位+简历分析
                # 若简历画像为空或上次解析失败兜底，先重抽简历，否则 build_analysis 拿不到结构化字段
                if _profile_needs_reparse(task.resume):
                    _reparse_resume(task, self.llm)
                elif task.resume:
                    sync_candidate_from_profile(task, task.resume.parsed_profile)
                analysis_payload = build_analysis(task, self.llm)
                analysis = task.analysis
                for field, value in analysis_payload.items():
                    setattr(analysis, field, value)
                analysis.save()
                if not task.regular_question_sets.exists():
                    generate_regular_questions(task, self.llm)
                if task.development_task_status != "not_enabled" and not task.development_tasks.exists():
                    generate_development_task(task, self.llm)
                task.overall_status = "pending_verification_confirmation"
                task.regular_question_status = "generated"
                if task.development_task_status == "pending_generation":
                    task.development_task_status = "reviewing"
                task.save(update_fields=["overall_status", "regular_question_status", "development_task_status", "updated_at"])
                result = analysis_payload
            elif job.job_type == "generate_regular_questions":
                question_set = generate_regular_questions(task, self.llm)
                task.regular_question_status = "generated"
                if task.overall_status in {"pending_analysis", "pending_verification_confirmation"}:
                    task.overall_status = "pending_question_review"
                task.save(update_fields=["regular_question_status", "overall_status", "updated_at"])
                result = {"question_set_id": question_set.id}
            elif job.job_type == "generate_development_task":
                dev_task = generate_development_task(task, self.llm)
                task.development_task_status = "reviewing"
                task.save(update_fields=["development_task_status", "updated_at"])
                result = {"development_task_id": dev_task.id}
            elif job.job_type == "score_regular_submission":
                payload = score_submission(task, "regular", self.llm)
                apply_score(task, "regular", payload)
                task.regular_question_status = "scored"
                task.overall_status = "pending_scoring" if task.development_task_status not in {"scored", "not_enabled"} else "pending_report_confirmation"
                task.save(update_fields=["regular_question_status", "overall_status", "updated_at"])
                result = payload
            elif job.job_type == "score_development_submission":
                payload = score_submission(task, "development", self.llm)
                apply_score(task, "development", payload)
                task.development_task_status = "scored"
                task.overall_status = "pending_report_confirmation" if task.regular_question_status == "scored" else "pending_scoring"
                task.save(update_fields=["development_task_status", "overall_status", "updated_at"])
                result = payload
            elif job.job_type == "generate_report":
                payload = build_report(task, self.llm)
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
                result = payload
            else:
                result = {"message": "export_document 请通过页面同步下载，本命令暂不处理该类型。"}
            job.status = "success"
            job.progress = 100
            job.result_json = result
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "progress", "result_json", "finished_at", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"完成任务 {job.id}: {job.job_type}"))
        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.finished_at = timezone.now()
            try:
                job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
            except OperationalError as save_exc:
                self.stderr.write(self.style.ERROR(f"任务失败且状态写回失败 {job.id}: {save_exc}"))
            self.stderr.write(self.style.ERROR(f"任务失败 {job.id}: {exc}"))
