import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.recruitment.models import AiJob, Evaluation
from services.analysis_workflow import build_analysis, generate_development_task, generate_regular_questions
from services.resume_parser import extract_pdf_text, summarize_resume_text
from services.scoring_workflow import build_report


class Command(BaseCommand):
    help = "运行轻量后台Worker，轮询 ai_job 表并处理队列任务。"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="只处理一个任务后退出")
        parser.add_argument("--sleep", type=float, default=2.0, help="没有任务时的等待秒数")

    def handle(self, *args, **options):
        while True:
            job = self.claim_job()
            if not job:
                if options["once"]:
                    self.stdout.write("没有待处理任务。")
                    return
                time.sleep(options["sleep"])
                continue
            self.execute(job)
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

    def execute(self, job):
        try:
            task = job.task
            result = {}
            if job.job_type == "analyze_position_resume":
                resume = task.resume
                resume.parse_status = "processing"
                resume.save(update_fields=["parse_status", "updated_at"])
                text = extract_pdf_text(resume.attachment.file.path)
                resume.resume_text = text
                resume.parsed_profile = summarize_resume_text(text)
                resume.parse_status = "success" if text else "failed"
                resume.parse_error = "" if text else "未能从PDF中抽取文本，可能是扫描件。"
                resume.parsed_at = timezone.now()
                resume.save()
                analysis_payload = build_analysis(task)
                analysis = task.analysis
                for field, value in analysis_payload.items():
                    setattr(analysis, field, value)
                analysis.save()
                if not task.regular_question_sets.exists():
                    generate_regular_questions(task)
                if task.development_task_status != "not_enabled" and not task.development_tasks.exists():
                    generate_development_task(task)
                task.overall_status = "pending_verification_confirmation"
                task.regular_question_status = "generated"
                task.save(update_fields=["overall_status", "regular_question_status", "updated_at"])
                result = analysis_payload
            elif job.job_type == "generate_regular_questions":
                question_set = generate_regular_questions(task)
                task.regular_question_status = "generated"
                task.save(update_fields=["regular_question_status", "updated_at"])
                result = {"question_set_id": question_set.id}
            elif job.job_type == "generate_development_task":
                dev_task = generate_development_task(task)
                task.development_task_status = "reviewing"
                task.save(update_fields=["development_task_status", "updated_at"])
                result = {"development_task_id": dev_task.id}
            elif job.job_type == "generate_report":
                payload = build_report(task)
                evaluation, _ = Evaluation.objects.get_or_create(task=task)
                evaluation.ai_suggestion = payload["ai_suggestion"]
                evaluation.skill_evaluations = payload["skill_evaluations"]
                evaluation.report_markdown = payload["report_markdown"]
                evaluation.save()
                result = payload
            else:
                result = {"message": "该任务类型已排队，但MVP暂未实现具体处理。"}
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
            job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
            self.stderr.write(self.style.ERROR(f"任务失败 {job.id}: {exc}"))
