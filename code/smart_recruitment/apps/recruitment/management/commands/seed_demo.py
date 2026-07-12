from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.recruitment.models import (
    Attachment,
    Candidate,
    Department,
    DevelopmentTask,
    Evaluation,
    Position,
    RecruitmentTask,
    RegularQuestionSet,
    Resume,
    TaskAnalysis,
)


class Command(BaseCommand):
    help = "创建演示账号和一条招聘评测任务，便于对照原型查看页面。"

    def handle(self, *args, **options):
        User = get_user_model()
        admin, _ = User.objects.get_or_create(username="admin", defaults={"is_staff": True, "is_superuser": True, "email": "admin@example.com"})
        admin.set_password("admin123456")
        admin.is_staff = True
        admin.is_superuser = True
        admin.save()

        tech, _ = User.objects.get_or_create(username="wang", defaults={"first_name": "王", "last_name": "招聘", "email": "wang@example.com"})
        tech.set_password("admin123456")
        tech.save()

        dept, _ = Department.objects.get_or_create(code="AI-RD", defaults={"name": "AI研发部"})
        position, _ = Position.objects.get_or_create(
            code="AI-DEV",
            defaults={
                "name": "AI应用开发工程师",
                "department": dept,
                "job_level": "middle",
                "raw_job_description": "熟练使用Python\n掌握Prompt设计\n熟悉RAG和Agent开发\n了解SQL注入与Prompt注入\n具备AI生成代码校验和测试能力",
                "status": "confirmed",
            },
        )
        candidate, _ = Candidate.objects.get_or_create(
            candidate_no="CAND-DEMO",
            defaults={"name": "张某某", "work_years": 4, "education": "本科", "current_company": "某科技公司"},
        )
        attachment, _ = Attachment.objects.get_or_create(
            original_name="张某某-简历.pdf",
            defaults={"file": "resumes/demo.pdf", "purpose": "resume", "file_size": 0, "uploaded_by": admin},
        )
        resume, _ = Resume.objects.get_or_create(
            candidate=candidate,
            version=1,
            defaults={"attachment": attachment, "parse_status": "success", "resume_text": "候选人有 Python、FastAPI、RAG 企业知识库项目经验。"},
        )
        task, _ = RecruitmentTask.objects.get_or_create(
            task_no="RT-20260715-001",
            defaults={
                "task_name": "AI应用开发工程师 - 张某某",
                "position": position,
                "candidate": candidate,
                "resume": resume,
                "department": dept,
                "hr_owner": admin,
                "technical_owner": tech,
                "planned_finish_at": timezone.now(),
                "overall_status": "pending_verification_confirmation",
                "regular_question_status": "generated",
                "development_task_status": "reviewing",
                "created_by": admin,
            },
        )
        TaskAnalysis.objects.get_or_create(
            task=task,
            defaults={
                "position_skills": [{"name": "Python", "weight": 30}, {"name": "Prompt设计", "weight": 20}, {"name": "RAG", "weight": 25}, {"name": "Agent", "weight": 25}],
                "resume_profile": {"summary": "具备Python与RAG项目经验"},
                "skill_matches": [
                    {"skill": "Python能力", "judgment": "basically_matched", "confidence": "high", "evidence": "简历中提到使用 Python + FastAPI 开发后端服务"},
                    {"skill": "RAG能力", "judgment": "needs_verification", "confidence": "medium", "evidence": "简历中提到企业知识问答系统，需要验证评估方法"},
                    {"skill": "Agent能力", "judgment": "not_mentioned", "confidence": "medium", "evidence": "简历未体现相关经验"},
                ],
                "verification_items": [
                    {"name": "RAG项目真实性", "selected_method": "项目追问", "priority": "高", "reason": "需确认项目职责"},
                    {"name": "RAG评估方法", "selected_method": "问答题", "priority": "高", "reason": "需确认评估指标"},
                    {"name": "Python工程能力", "selected_method": "代码阅读题", "priority": "中", "reason": "需验证工程质量"},
                ],
            },
        )
        RegularQuestionSet.objects.get_or_create(
            task=task,
            version=1,
            defaults={
                "status": "reviewing",
                "duration_minutes": 30,
                "questions": [
                    {"content": "Python列表推导式", "skill": "Python", "difficulty": "middle", "reference_answer": "考察Python基础语法", "scoring_points": ["语法", "可读性"]},
                    {"content": "SQL注入风险", "skill": "安全", "difficulty": "middle", "reference_answer": "识别拼接SQL风险并使用参数化查询", "scoring_points": ["风险点", "攻击方式", "修复方案"]},
                    {"content": "Redis缓存穿透", "skill": "工程", "difficulty": "middle", "reference_answer": "布隆过滤器或空值缓存", "scoring_points": ["原因", "方案"]},
                ],
            },
        )
        DevelopmentTask.objects.get_or_create(
            task=task,
            version=1,
            defaults={
                "status": "pending_send",
                "content": {
                    "background": "企业制度问答辅助模块",
                    "duration": "3天",
                    "requirements": ["实现文档读取与问答", "展示引用来源", "提供README"],
                    "deliverables": ["源码压缩包", "README", "运行截图"],
                    "acceptance_criteria": ["功能完整性", "代码质量", "安全与校验"],
                },
            },
        )
        Evaluation.objects.get_or_create(task=task, defaults={"regular_score": 78, "development_score": 82, "final_score": 80, "recommendation": "yes"})
        self.stdout.write(self.style.SUCCESS("演示数据已准备好：admin / admin123456"))
