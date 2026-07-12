from django.urls import path

from . import views


urlpatterns = [
    path("", views.task_list, name="task-list"),
    path("new/", views.task_create, name="task-create"),
    path("position-templates/", views.position_template_list, name="position-template-list"),
    path("position-templates/<int:pk>/json/", views.position_template_json, name="position-template-json"),
    path("position-templates/<int:pk>/", views.position_template_list, name="position-template-detail"),
    path("<int:pk>/", views.task_detail, name="task-detail"),
    path("<int:pk>/parsing/", views.task_parsing, name="task-parsing"),
    path("<int:pk>/parsing/status/", views.task_parsing_status, name="task-parsing-status"),
    path("<int:pk>/candidate/", views.candidate_confirm, name="candidate-confirm"),
    path("<int:pk>/analysis/", views.analysis_view, name="task-analysis"),
    path("<int:pk>/regular-questions/", views.regular_questions_view, name="regular-questions"),
    path("<int:pk>/development-task/", views.development_task_view, name="development-task"),
    path("<int:pk>/report/", views.report_view, name="task-report"),
    path("<int:pk>/submission/", views.upload_submission, name="upload-submission"),
    path("<int:pk>/enqueue/<str:job_type>/", views.enqueue_job, name="enqueue-job"),
    path("<int:pk>/run-now/<str:job_type>/", views.run_ai_action_now, name="run-ai-now"),
    path("<int:pk>/export/regular/<str:fmt>/", views.export_regular_questions, name="export-regular"),
    path("<int:pk>/export/development/<str:fmt>/", views.export_development_task, name="export-development"),
    path("<int:pk>/export/report/<str:fmt>/", views.export_report, name="export-report"),
]
