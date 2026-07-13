from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.http import content_disposition_header

from .models import Candidate, Department, DevelopmentTask, Position, RecruitmentTask, RegularQuestionSet


class RegularQuestionConfirmationTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="reviewer", password="test-password")
        self.department = Department.objects.create(code="TEST-DEPT", name="测试部门")
        self.position = Position.objects.create(
            code="TEST-POS",
            name="测试工程师",
            department=self.department,
            raw_job_description="测试岗位要求",
            status="confirmed",
        )
        self.candidate = Candidate.objects.create(candidate_no="TEST-CAND", name="测试候选人")
        self.task = RecruitmentTask.objects.create(
            task_no="TEST-TASK",
            task_name="测试招聘评测任务",
            position=self.position,
            candidate=self.candidate,
            department=self.department,
            hr_owner=self.user,
            technical_owner=self.user,
            overall_status="pending_question_review",
            regular_question_status="generated",
            development_task_status="reviewing",
        )
        DevelopmentTask.objects.create(
            task=self.task,
            version=1,
            status="reviewing",
            content={"title": "测试开发题"},
        )
        self.question_set = RegularQuestionSet.objects.create(
            task=self.task,
            version=1,
            status="reviewing",
            questions=[
                {
                    "type": "basic",
                    "content": "第一题",
                    "status": "pending",
                    "scoring_points": ["评分点一"],
                },
                {
                    "type": "qa",
                    "content": "第二题",
                    "status": "confirmed",
                    "scoring_points": ["评分点二"],
                },
            ],
        )
        self.client.force_login(self.user)
        self.url = reverse("regular-questions", args=[self.task.pk])

    def test_confirm_marks_question_set_task_and_every_question_confirmed(self):
        response = self.client.post(self.url, {"action": "confirm"})

        self.assertRedirects(response, reverse("development-task", args=[self.task.pk]))
        self.task.refresh_from_db()
        self.question_set.refresh_from_db()

        self.assertEqual(self.task.regular_question_status, "confirmed")
        self.assertEqual(self.task.overall_status, "pending_delivery")
        self.assertEqual(self.question_set.status, "confirmed")
        self.assertTrue(all(question.get("status") == "confirmed" for question in self.question_set.questions))

        page = self.client.get(self.url)
        self.assertContains(page, "✓ 题目已确认")
        self.assertContains(page, "普通题已全部确认并锁定")
        self.assertNotContains(page, 'name="action" value="confirm"')
        self.assertNotContains(page, 'name="action" value="delete"')

    def test_repeated_confirm_repairs_legacy_question_status_and_redirects(self):
        self.task.regular_question_status = "confirmed"
        self.task.overall_status = "pending_delivery"
        self.task.save(update_fields=["regular_question_status", "overall_status", "updated_at"])
        self.question_set.status = "confirmed"
        self.question_set.questions = [
            {
                "type": "basic",
                "content": "历史题目",
                "status": "pending",
                "scoring_points": ["评分点"],
            }
        ]
        self.question_set.save(update_fields=["status", "questions", "updated_at"])

        response = self.client.post(self.url, {"action": "confirm"})

        self.assertRedirects(response, reverse("development-task", args=[self.task.pk]))
        self.question_set.refresh_from_db()
        self.assertEqual(self.question_set.questions[0]["status"], "confirmed")

    def test_confirmed_question_set_cannot_be_modified(self):
        self.task.regular_question_status = "confirmed"
        self.task.overall_status = "pending_delivery"
        self.task.save(update_fields=["regular_question_status", "overall_status", "updated_at"])
        self.question_set.status = "confirmed"
        self.question_set.save(update_fields=["status", "updated_at"])

        original_count = len(self.question_set.questions)
        response = self.client.post(self.url, {"action": "delete", "question_index": 0})

        self.assertRedirects(response, self.url)
        self.question_set.refresh_from_db()
        self.assertEqual(len(self.question_set.questions), original_count)
        self.assertTrue(all(question.get("status") == "confirmed" for question in self.question_set.questions))

    @patch("services.export_document.timezone.localdate", return_value=date(2026, 7, 12))
    def test_all_word_and_pdf_downloads_use_consistent_business_filename(self, _mock_localdate):
        self.candidate.name = "韩玲"
        self.candidate.save(update_fields=["name", "updated_at"])
        self.question_set.version = 3
        self.question_set.save(update_fields=["version", "updated_at"])
        development_task = self.task.development_tasks.get()
        development_task.version = 2
        development_task.save(update_fields=["version", "updated_at"])

        cases = [
            (reverse("export-regular", args=[self.task.pk, "docx"]) + "?with_answers=1", "普通题用人部门版-韩玲-20260712-v3.docx"),
            (reverse("export-regular", args=[self.task.pk, "pdf"]) + "?with_answers=1", "普通题用人部门版-韩玲-20260712-v3.pdf"),
            (reverse("export-regular", args=[self.task.pk, "docx"]), "普通题候选人版-韩玲-20260712-v3.docx"),
            (reverse("export-regular", args=[self.task.pk, "pdf"]), "普通题候选人版-韩玲-20260712-v3.pdf"),
            (reverse("export-development", args=[self.task.pk, "docx"]), "现场开发题-韩玲-20260712-v2.docx"),
            (reverse("export-development", args=[self.task.pk, "pdf"]), "现场开发题-韩玲-20260712-v2.pdf"),
            (reverse("export-report", args=[self.task.pk, "docx"]), "评测报告-韩玲-20260712-v1.docx"),
            (reverse("export-report", args=[self.task.pk, "pdf"]), "评测报告-韩玲-20260712-v1.pdf"),
        ]

        for url, expected_filename in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response["Content-Disposition"],
                    content_disposition_header(as_attachment=True, filename=expected_filename),
                )
                self.assertGreater(int(response["Content-Length"]), 0)
