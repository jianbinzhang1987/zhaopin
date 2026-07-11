from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        abstract = True


class Department(TimeStampedModel):
    code = models.CharField("部门编码", max_length=64, unique=True)
    name = models.CharField("部门名称", max_length=128)
    parent = models.ForeignKey("self", verbose_name="上级部门", null=True, blank=True, on_delete=models.SET_NULL)
    enabled = models.BooleanField("启用", default=True)

    class Meta:
        verbose_name = "部门"
        verbose_name_plural = "部门"

    def __str__(self) -> str:
        return self.name


class Position(TimeStampedModel):
    LEVEL_CHOICES = [
        ("junior", "初级"),
        ("middle", "中级"),
        ("senior", "高级"),
        ("expert", "专家"),
    ]
    STATUS_CHOICES = [
        ("draft", "草稿"),
        ("confirmed", "已确认"),
        ("disabled", "停用"),
    ]

    code = models.CharField("岗位编码", max_length=64, unique=True)
    name = models.CharField("岗位名称", max_length=128)
    department = models.ForeignKey(Department, verbose_name="用人部门", null=True, blank=True, on_delete=models.SET_NULL)
    job_level = models.CharField("岗位级别", max_length=20, choices=LEVEL_CHOICES, default="middle")
    raw_job_description = models.TextField("岗位要求")
    summary = models.TextField("岗位摘要", blank=True)
    version = models.PositiveIntegerField("版本", default=1)
    status = models.CharField("状态", max_length=20, choices=STATUS_CHOICES, default="draft")

    class Meta:
        verbose_name = "岗位"
        verbose_name_plural = "岗位"

    def __str__(self) -> str:
        return f"{self.name} v{self.version}"


class PositionSkill(TimeStampedModel):
    LEVEL_CHOICES = [
        ("understand", "了解"),
        ("master", "掌握"),
        ("proficient", "熟练"),
        ("expert", "专家"),
    ]

    position = models.ForeignKey(Position, related_name="skills", verbose_name="岗位", on_delete=models.CASCADE)
    code = models.CharField("能力编码", max_length=64)
    name = models.CharField("能力名称", max_length=128)
    category = models.CharField("能力分类", max_length=64)
    requirement_level = models.CharField("要求等级", max_length=20, choices=LEVEL_CHOICES)
    weight_percent = models.DecimalField("权重", max_digits=5, decimal_places=2, default=0)
    must_verify = models.BooleanField("必须验证", default=True)
    preferred_methods = models.JSONField("推荐验证方式", default=list)
    description = models.TextField("要求说明", blank=True)
    sort_no = models.PositiveIntegerField("排序", default=0)

    class Meta:
        unique_together = [("position", "code")]
        ordering = ["sort_no", "id"]
        verbose_name = "岗位能力项"
        verbose_name_plural = "岗位能力项"

    def __str__(self) -> str:
        return self.name


class Candidate(TimeStampedModel):
    candidate_no = models.CharField("候选人编号", max_length=64, unique=True)
    name = models.CharField("姓名", max_length=128)
    work_years = models.DecimalField("工作年限", max_digits=4, decimal_places=1, null=True, blank=True)
    education = models.CharField("学历", max_length=32, blank=True)
    current_company = models.CharField("当前公司", max_length=128, blank=True)
    current_position = models.CharField("当前职位", max_length=128, blank=True)
    email = models.EmailField("邮箱", blank=True)
    mobile = models.CharField("手机", max_length=32, blank=True)
    source_channel = models.CharField("来源渠道", max_length=64, blank=True)

    class Meta:
        verbose_name = "候选人"
        verbose_name_plural = "候选人"

    def __str__(self) -> str:
        return self.name


class Attachment(TimeStampedModel):
    SECURITY_CHOICES = [
        ("public", "公开"),
        ("internal", "内部"),
        ("confidential", "机密"),
    ]
    PURPOSE_CHOICES = [
        ("resume", "简历"),
        ("regular_submission", "普通题答卷"),
        ("development_submission", "开发题提交"),
        ("export", "导出文件"),
        ("other", "其他"),
    ]

    file = models.FileField("文件", upload_to="uploads/%Y/%m/")
    original_name = models.CharField("原文件名", max_length=256)
    purpose = models.CharField("用途", max_length=32, choices=PURPOSE_CHOICES, default="other")
    mime_type = models.CharField("MIME", max_length=128, blank=True)
    file_size = models.PositiveBigIntegerField("文件大小", default=0)
    sha256 = models.CharField("SHA256", max_length=64, blank=True)
    security_level = models.CharField("安全级别", max_length=20, choices=SECURITY_CHOICES, default="internal")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="上传人", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        verbose_name = "附件"
        verbose_name_plural = "附件"

    def __str__(self) -> str:
        return self.original_name


class Resume(TimeStampedModel):
    PARSE_STATUS_CHOICES = [
        ("pending", "待解析"),
        ("processing", "解析中"),
        ("success", "解析成功"),
        ("failed", "解析失败"),
    ]

    candidate = models.ForeignKey(Candidate, related_name="resumes", verbose_name="候选人", on_delete=models.CASCADE)
    attachment = models.ForeignKey(Attachment, verbose_name="简历文件", on_delete=models.PROTECT)
    version = models.PositiveIntegerField("版本", default=1)
    parse_status = models.CharField("解析状态", max_length=20, choices=PARSE_STATUS_CHOICES, default="pending")
    resume_text = models.TextField("简历文本", blank=True)
    parsed_profile = models.JSONField("解析画像", default=dict)
    parse_error = models.TextField("解析错误", blank=True)
    parsed_at = models.DateTimeField("解析时间", null=True, blank=True)

    class Meta:
        unique_together = [("candidate", "version")]
        verbose_name = "简历"
        verbose_name_plural = "简历"

    def __str__(self) -> str:
        return f"{self.candidate.name} 简历 v{self.version}"


class RecruitmentTask(TimeStampedModel):
    OVERALL_STATUS = [
        ("draft", "草稿"),
        ("pending_analysis", "待分析"),
        ("pending_verification_confirmation", "待确认验证项"),
        ("pending_question_review", "待审核题目"),
        ("pending_delivery", "待交付"),
        ("candidate_in_progress", "候选人进行中"),
        ("pending_collection", "待回收"),
        ("pending_scoring", "待评分"),
        ("pending_report_confirmation", "待确认报告"),
        ("completed", "已完成"),
        ("cancelled", "已取消"),
    ]
    REGULAR_STATUS = [
        ("not_generated", "未生成"),
        ("generated", "已生成"),
        ("reviewing", "审核中"),
        ("confirmed", "已确认"),
        ("exported", "已导出"),
        ("collected", "已回收"),
        ("scored", "已评分"),
    ]
    DEVELOPMENT_STATUS = [
        ("not_enabled", "未启用"),
        ("pending_generation", "待生成"),
        ("reviewing", "审核中"),
        ("pending_send", "待发送"),
        ("in_progress", "进行中"),
        ("collected", "已回收"),
        ("scored", "已评分"),
    ]

    task_no = models.CharField("任务编号", max_length=64, unique=True)
    task_name = models.CharField("任务名称", max_length=256)
    position = models.ForeignKey(Position, verbose_name="岗位", on_delete=models.PROTECT)
    candidate = models.ForeignKey(Candidate, verbose_name="候选人", on_delete=models.PROTECT)
    resume = models.ForeignKey(Resume, verbose_name="简历", on_delete=models.PROTECT)
    department = models.ForeignKey(Department, verbose_name="用人部门", null=True, blank=True, on_delete=models.SET_NULL)
    hr_owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="hr_tasks", verbose_name="HR负责人", on_delete=models.PROTECT)
    technical_owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="technical_tasks", verbose_name="技术负责人", on_delete=models.PROTECT)
    planned_finish_at = models.DateTimeField("计划完成时间", null=True, blank=True)
    overall_status = models.CharField("主状态", max_length=40, choices=OVERALL_STATUS, default="pending_analysis")
    regular_question_status = models.CharField("普通题状态", max_length=32, choices=REGULAR_STATUS, default="not_generated")
    development_task_status = models.CharField("开发题状态", max_length=32, choices=DEVELOPMENT_STATUS, default="not_enabled")
    regular_weight_percent = models.DecimalField("普通题权重", max_digits=5, decimal_places=2, default=40)
    development_weight_percent = models.DecimalField("开发题权重", max_digits=5, decimal_places=2, default=60)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="created_recruitment_tasks", verbose_name="创建人", null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "招聘评测任务"
        verbose_name_plural = "招聘评测任务"

    def __str__(self) -> str:
        return self.task_name

    @property
    def next_action(self) -> str:
        mapping = {
            "pending_analysis": "等待分析",
            "pending_verification_confirmation": "确认验证项",
            "pending_question_review": "审核题目",
            "pending_delivery": "交付题目",
            "candidate_in_progress": "跟进候选人",
            "pending_collection": "上传回收结果",
            "pending_scoring": "评分",
            "pending_report_confirmation": "确认报告",
            "completed": "查看报告",
        }
        return mapping.get(self.overall_status, "继续处理")


class TaskAnalysis(TimeStampedModel):
    task = models.OneToOneField(RecruitmentTask, related_name="analysis", verbose_name="任务", on_delete=models.CASCADE)
    position_skills = models.JSONField("岗位能力模型", default=list)
    resume_profile = models.JSONField("简历画像", default=dict)
    skill_matches = models.JSONField("能力匹配", default=list)
    verification_items = models.JSONField("待验证项", default=list)
    confirmed = models.BooleanField("已确认", default=False)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="确认人", null=True, blank=True, on_delete=models.SET_NULL)
    confirmed_at = models.DateTimeField("确认时间", null=True, blank=True)

    class Meta:
        verbose_name = "任务分析"
        verbose_name_plural = "任务分析"


class RegularQuestionSet(TimeStampedModel):
    STATUS_CHOICES = [
        ("draft", "草稿"),
        ("reviewing", "审核中"),
        ("confirmed", "已确认"),
        ("exported", "已导出"),
    ]

    task = models.ForeignKey(RecruitmentTask, related_name="regular_question_sets", verbose_name="任务", on_delete=models.CASCADE)
    version = models.PositiveIntegerField("版本", default=1)
    duration_minutes = models.PositiveIntegerField("建议时长", default=60)
    status = models.CharField("状态", max_length=20, choices=STATUS_CHOICES, default="draft")
    questions = models.JSONField("题目", default=list)
    review_notes = models.TextField("审核意见", blank=True)

    class Meta:
        unique_together = [("task", "version")]
        ordering = ["-version"]
        verbose_name = "普通题目集"
        verbose_name_plural = "普通题目集"


class DevelopmentTask(TimeStampedModel):
    STATUS_CHOICES = [
        ("draft", "草稿"),
        ("reviewing", "审核中"),
        ("pending_send", "待发送"),
        ("sent", "已发送"),
        ("collected", "已回收"),
        ("scored", "已评分"),
    ]

    task = models.ForeignKey(RecruitmentTask, related_name="development_tasks", verbose_name="任务", on_delete=models.CASCADE)
    version = models.PositiveIntegerField("版本", default=1)
    status = models.CharField("状态", max_length=20, choices=STATUS_CHOICES, default="draft")
    content = models.JSONField("开发题内容", default=dict)
    deadline = models.DateTimeField("截止时间", null=True, blank=True)
    sent_at = models.DateTimeField("发送时间", null=True, blank=True)
    collected_at = models.DateTimeField("回收时间", null=True, blank=True)

    class Meta:
        unique_together = [("task", "version")]
        ordering = ["-version"]
        verbose_name = "现场开发题"
        verbose_name_plural = "现场开发题"


class Submission(TimeStampedModel):
    TYPE_CHOICES = [
        ("regular", "普通题"),
        ("development", "开发题"),
    ]
    INTEGRITY_CHOICES = [
        ("unchecked", "未检查"),
        ("complete", "完整"),
        ("incomplete", "不完整"),
    ]

    task = models.ForeignKey(RecruitmentTask, related_name="submissions", verbose_name="任务", on_delete=models.CASCADE)
    submission_type = models.CharField("提交类型", max_length=20, choices=TYPE_CHOICES)
    submitted_at = models.DateTimeField("提交时间", default=timezone.now)
    integrity_status = models.CharField("完整性", max_length=20, choices=INTEGRITY_CHOICES, default="unchecked")
    notes = models.TextField("备注", blank=True)
    attachments = models.ManyToManyField(Attachment, verbose_name="附件", blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        verbose_name = "候选人提交"
        verbose_name_plural = "候选人提交"


class Evaluation(TimeStampedModel):
    RECOMMENDATION_CHOICES = [
        ("strong_yes", "强烈推荐"),
        ("yes", "推荐"),
        ("hold", "待定"),
        ("no", "不推荐"),
    ]

    task = models.OneToOneField(RecruitmentTask, related_name="evaluation", verbose_name="任务", on_delete=models.CASCADE)
    regular_score = models.DecimalField("普通题得分", max_digits=5, decimal_places=2, null=True, blank=True)
    development_score = models.DecimalField("开发题得分", max_digits=5, decimal_places=2, null=True, blank=True)
    final_score = models.DecimalField("最终得分", max_digits=5, decimal_places=2, null=True, blank=True)
    ai_suggestion = models.JSONField("AI建议", default=dict)
    skill_evaluations = models.JSONField("能力评价", default=list)
    strengths = models.TextField("优势", blank=True)
    risks = models.TextField("风险", blank=True)
    recommendation = models.CharField("建议结论", max_length=20, choices=RECOMMENDATION_CHOICES, blank=True)
    report_markdown = models.TextField("报告正文", blank=True)
    confirmed = models.BooleanField("已确认", default=False)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="确认人", null=True, blank=True, on_delete=models.SET_NULL)
    confirmed_at = models.DateTimeField("确认时间", null=True, blank=True)

    class Meta:
        verbose_name = "评测报告"
        verbose_name_plural = "评测报告"


class AiJob(TimeStampedModel):
    STATUS_CHOICES = [
        ("queued", "排队中"),
        ("running", "运行中"),
        ("success", "成功"),
        ("failed", "失败"),
        ("cancelled", "已取消"),
    ]
    TYPE_CHOICES = [
        ("analyze_position_resume", "岗位简历分析"),
        ("generate_regular_questions", "生成普通题"),
        ("generate_development_task", "生成开发题"),
        ("score_regular_submission", "普通题评分"),
        ("score_development_submission", "开发题评分"),
        ("generate_report", "生成报告"),
        ("export_document", "导出文档"),
    ]

    task = models.ForeignKey(RecruitmentTask, related_name="ai_jobs", verbose_name="任务", null=True, blank=True, on_delete=models.CASCADE)
    job_type = models.CharField("任务类型", max_length=40, choices=TYPE_CHOICES)
    status = models.CharField("状态", max_length=20, choices=STATUS_CHOICES, default="queued")
    progress = models.PositiveSmallIntegerField("进度", default=0)
    input_json = models.JSONField("输入", default=dict)
    result_json = models.JSONField("结果", default=dict)
    retry_count = models.PositiveSmallIntegerField("重试次数", default=0)
    error_message = models.TextField("错误", blank=True)
    started_at = models.DateTimeField("开始时间", null=True, blank=True)
    finished_at = models.DateTimeField("结束时间", null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "AI后台任务"
        verbose_name_plural = "AI后台任务"


class AuditEvent(models.Model):
    task = models.ForeignKey(RecruitmentTask, related_name="events", verbose_name="任务", null=True, blank=True, on_delete=models.CASCADE)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="操作人", null=True, blank=True, on_delete=models.SET_NULL)
    event_type = models.CharField("事件类型", max_length=64)
    message = models.CharField("说明", max_length=512)
    metadata = models.JSONField("元数据", default=dict)
    created_at = models.DateTimeField("创建时间", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "审计日志"
        verbose_name_plural = "审计日志"
