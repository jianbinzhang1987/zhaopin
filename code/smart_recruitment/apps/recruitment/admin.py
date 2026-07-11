from django.contrib import admin

from .models import (
    AiJob,
    Attachment,
    AuditEvent,
    Candidate,
    Department,
    DevelopmentTask,
    Evaluation,
    Position,
    PositionSkill,
    RecruitmentTask,
    RegularQuestionSet,
    Resume,
    Submission,
    TaskAnalysis,
)


@admin.register(RecruitmentTask)
class RecruitmentTaskAdmin(admin.ModelAdmin):
    list_display = ("task_no", "task_name", "candidate", "position", "overall_status", "regular_question_status", "development_task_status", "updated_at")
    list_filter = ("overall_status", "regular_question_status", "development_task_status")
    search_fields = ("task_no", "task_name", "candidate__name", "position__name")


admin.site.register(Department)
admin.site.register(Position)
admin.site.register(PositionSkill)
admin.site.register(Candidate)
admin.site.register(Attachment)
admin.site.register(Resume)
admin.site.register(TaskAnalysis)
admin.site.register(RegularQuestionSet)
admin.site.register(DevelopmentTask)
admin.site.register(Submission)
admin.site.register(Evaluation)
admin.site.register(AiJob)
admin.site.register(AuditEvent)

