from django import forms
from django.contrib.auth import get_user_model
from uuid import uuid4

from .models import Attachment, Candidate, Department, Evaluation, Position, PositionTemplate, RecruitmentTask, Submission


class TaskCreateForm(forms.Form):
    position_template = forms.ModelChoiceField(label="岗位模板", queryset=PositionTemplate.objects.none(), required=False)
    position_name = forms.CharField(label="岗位名称", max_length=128)
    department_name = forms.CharField(label="用人部门", max_length=128)
    job_level = forms.ChoiceField(label="岗位级别", choices=Position.LEVEL_CHOICES)
    raw_job_description = forms.CharField(label="岗位要求", widget=forms.Textarea(attrs={"rows": 8}))
    resume_file = forms.FileField(label="PDF简历", required=False)
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
        self.fields["position_template"].queryset = PositionTemplate.objects.filter(status="published").select_related("department").order_by("name")
        self.fields["position_template"].widget.attrs.update({"id": "id_position_template", "data-url-suffix": ""})

    def save(self, user):
        department_name = self.cleaned_data["department_name"].strip()
        department, _ = Department.objects.get_or_create(
            name=department_name,
            defaults={"code": f"DEPT-{uuid4().hex[:12].upper()}"},
        )
        position = Position.objects.create(
            code=f"POS-{uuid4().hex[:12].upper()}",
            name=self.cleaned_data["position_name"],
            department=department,
            job_level=self.cleaned_data["job_level"],
            raw_job_description=self.cleaned_data["raw_job_description"],
            status="confirmed",
        )
        # 第1步仅建立候选人占位记录，候选人详细信息由第2步确认
        candidate = Candidate.objects.create(
            candidate_no=f"CAND-{uuid4().hex[:12].upper()}",
            name="（待补充）",
        )
        resume = None
        resume_upload = self.cleaned_data.get("resume_file")
        if resume_upload:
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
            task_no=f"RT-{uuid4().hex[:12].upper()}",
            task_name=f"{position.name} - （待补充）",
            position=position,
            candidate=candidate,
            resume=resume,
            department=department,
            hr_owner=user,
            technical_owner=self.cleaned_data["technical_owner"],
            planned_finish_at=self.cleaned_data["planned_finish_at"],
            overall_status="draft",
            development_task_status="pending_generation" if self.cleaned_data["enable_development_task"] else "not_enabled",
            created_by=user,
        )
        return task


class CandidateConfirmForm(forms.Form):
    candidate_name = forms.CharField(label="候选人姓名", max_length=128)
    work_years = forms.DecimalField(
        label="工作年限",
        required=False,
        min_value=0,
        max_digits=4,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"placeholder": "如：4", "step": "0.1"}),
    )
    EDUCATION_CHOICES = [
        ("", "请选择"),
        ("high_school", "高中/中专"),
        ("college", "大专"),
        ("bachelor", "本科"),
        ("master", "硕士"),
        ("phd", "博士"),
    ]
    education = forms.ChoiceField(label="学历", choices=EDUCATION_CHOICES, required=False)
    current_company = forms.CharField(label="当前公司", max_length=128, required=False, widget=forms.TextInput(attrs={"placeholder": "请输入当前公司"}))
    current_position = forms.CharField(label="当前职位", max_length=128, required=False, widget=forms.TextInput(attrs={"placeholder": "请输入当前职位"}))
    candidate_email = forms.EmailField(label="候选人邮箱", required=False)
    candidate_mobile = forms.CharField(label="候选人手机", max_length=32, required=False)
    skills_text = forms.CharField(
        label="技能标签",
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "多技能以顿号分隔，如 Python、RAG、Agent"}),
    )

    def save(self, task, user):
        candidate = task.candidate
        candidate.name = self.cleaned_data["candidate_name"]
        candidate.work_years = self.cleaned_data.get("work_years")
        candidate.education = self.cleaned_data.get("education", "")
        candidate.current_company = self.cleaned_data.get("current_company", "")
        candidate.current_position = self.cleaned_data.get("current_position", "")
        candidate.email = self.cleaned_data["candidate_email"]
        candidate.mobile = self.cleaned_data["candidate_mobile"]
        candidate.save()
        # 技能标签写入简历画像
        if task.resume:
            profile = dict(task.resume.parsed_profile or {})
            skills = split_tags(self.cleaned_data.get("skills_text", ""))
            profile["skills"] = skills
            profile.setdefault("_ai", {})
            if "_ai" in profile and isinstance(profile["_ai"], dict):
                profile["_ai"]["skills_edited"] = True
            task.resume.parsed_profile = profile
            task.resume.save(update_fields=["parsed_profile", "updated_at"])
        task.task_name = f"{task.position.name} - {candidate.name}"
        task.save(update_fields=["task_name", "updated_at"])
        return task


class PositionTemplateForm(forms.ModelForm):
    responsibilities_text = forms.CharField(label="岗位职责", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    requirements_text = forms.CharField(label="任职要求", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    technical_tags_text = forms.CharField(label="技术方向", required=False)
    keywords_text = forms.CharField(label="岗位关键词", required=False)

    class Meta:
        model = PositionTemplate
        fields = [
            "name",
            "department",
            "job_level",
            "scenario",
            "description",
            "responsibilities_text",
            "requirements_text",
            "technical_tags_text",
            "keywords_text",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        if instance and instance.pk:
            self.fields["responsibilities_text"].initial = "\n".join(instance.responsibilities or [])
            self.fields["requirements_text"].initial = "\n".join(instance.requirements or [])
            self.fields["technical_tags_text"].initial = "、".join(instance.technical_tags or [])
            self.fields["keywords_text"].initial = "、".join(instance.keywords or [])

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.responsibilities = split_lines(self.cleaned_data.get("responsibilities_text", ""))
        instance.requirements = split_lines(self.cleaned_data.get("requirements_text", ""))
        instance.technical_tags = split_tags(self.cleaned_data.get("technical_tags_text", ""))
        instance.keywords = split_tags(self.cleaned_data.get("keywords_text", ""))
        if commit:
            instance.save()
        return instance


def split_lines(value: str) -> list[str]:
    return [line.strip().lstrip("0123456789.、)） ").strip() for line in value.splitlines() if line.strip()]


def split_tags(value: str) -> list[str]:
    normalized = value.replace(",", "、").replace("，", "、").replace(" ", "、")
    return [item.strip() for item in normalized.split("、") if item.strip()]


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
        ]
        widgets = {
            "regular_score": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "development_score": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "final_score": forms.NumberInput(attrs={"step": "0.01", "min": "0", "max": "100"}),
            "strengths": forms.Textarea(attrs={"rows": 3}),
            "risks": forms.Textarea(attrs={"rows": 3}),
            "report_markdown": forms.Textarea(attrs={"rows": 10}),
        }
