from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import Attachment, Candidate, Department, Position, RecruitmentTask, Submission, Evaluation


class TaskCreateForm(forms.Form):
    position_name = forms.CharField(label="岗位名称", max_length=128)
    department_name = forms.CharField(label="用人部门", max_length=128)
    job_level = forms.ChoiceField(label="岗位级别", choices=Position.LEVEL_CHOICES)
    raw_job_description = forms.CharField(label="岗位要求", widget=forms.Textarea(attrs={"rows": 8}))
    candidate_name = forms.CharField(label="候选人姓名", max_length=128)
    candidate_mobile = forms.CharField(label="候选人手机", max_length=32, required=False)
    candidate_email = forms.EmailField(label="候选人邮箱", required=False)
    resume_file = forms.FileField(label="PDF简历")
    technical_owner = forms.ModelChoiceField(label="技术审核人", queryset=get_user_model().objects.none())
    planned_finish_at = forms.DateTimeField(
        label="计划完成时间",
        required=False,
        input_formats=["%Y-%m-%d %H:%M", "%Y-%m-%d"],
        widget=forms.DateTimeInput(attrs={"placeholder": "2026-07-20 18:00"}),
    )
    enable_development_task = forms.BooleanField(label="启用现场开发题", required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["technical_owner"].queryset = get_user_model().objects.filter(is_active=True).order_by("username")

    def save(self, user):
        department_name = self.cleaned_data["department_name"].strip()
        department, _ = Department.objects.get_or_create(
            name=department_name,
            defaults={"code": f"DEPT-{timezone.now():%Y%m%d%H%M%S}"},
        )
        position = Position.objects.create(
            code=f"POS-{timezone.now():%Y%m%d%H%M%S}",
            name=self.cleaned_data["position_name"],
            department=department,
            job_level=self.cleaned_data["job_level"],
            raw_job_description=self.cleaned_data["raw_job_description"],
            status="confirmed",
        )
        candidate = Candidate.objects.create(
            candidate_no=f"CAND-{timezone.now():%Y%m%d%H%M%S}",
            name=self.cleaned_data["candidate_name"],
            mobile=self.cleaned_data["candidate_mobile"],
            email=self.cleaned_data["candidate_email"],
        )
        resume_upload = self.cleaned_data["resume_file"]
        attachment = Attachment.objects.create(
            file=resume_upload,
            original_name=resume_upload.name,
            purpose="resume",
            mime_type=getattr(resume_upload, "content_type", "") or "",
            file_size=getattr(resume_upload, "size", 0) or 0,
            uploaded_by=user,
        )
        resume = candidate.resumes.create(attachment=attachment)
        task = RecruitmentTask.objects.create(
            task_no=f"RT-{timezone.now():%Y%m%d%H%M%S}",
            task_name=f"{position.name} - {candidate.name}",
            position=position,
            candidate=candidate,
            resume=resume,
            department=department,
            hr_owner=user,
            technical_owner=self.cleaned_data["technical_owner"],
            planned_finish_at=self.cleaned_data["planned_finish_at"],
            development_task_status="pending_generation" if self.cleaned_data["enable_development_task"] else "not_enabled",
            created_by=user,
        )
        return task


class SubmissionForm(forms.ModelForm):
    files = forms.FileField(label="提交附件", required=False)

    class Meta:
        model = Submission
        fields = ["submission_type", "integrity_status", "notes"]


class EvaluationForm(forms.ModelForm):
    class Meta:
        model = Evaluation
        fields = [
            "regular_score",
            "development_score",
            "final_score",
            "strengths",
            "risks",
            "recommendation",
            "report_markdown",
            "confirmed",
        ]
        widgets = {
            "strengths": forms.Textarea(attrs={"rows": 3}),
            "risks": forms.Textarea(attrs={"rows": 3}),
            "report_markdown": forms.Textarea(attrs={"rows": 10}),
        }

