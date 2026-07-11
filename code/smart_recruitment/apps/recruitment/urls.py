from django.urls import path

from . import views


urlpatterns = [
    path("", views.task_list, name="task-list"),
    path("new/", views.task_create, name="task-create"),
    path("<int:pk>/", views.task_detail, name="task-detail"),
    path("<int:pk>/analysis/", views.analysis_view, name="task-analysis"),
    path("<int:pk>/regular-questions/", views.regular_questions_view, name="regular-questions"),
    path("<int:pk>/development-task/", views.development_task_view, name="development-task"),
    path("<int:pk>/report/", views.report_view, name="task-report"),
    path("<int:pk>/enqueue/<str:job_type>/", views.enqueue_job, name="enqueue-job"),
]

