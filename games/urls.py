from django.contrib.auth.views import LogoutView
from django.urls import path
from . import views


urlpatterns = [
    path('health/', views.health, name='health'),
    path('health/worker/', views.health, {'worker': True}, name='worker_health'),
    path('', views.home, name='home'),
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),
    path('refresh/', views.refresh, name='refresh'),
    path('jobs/<int:job_id>/', views.job_detail, name='job_detail'),
    path('jobs/<int:job_id>/cancel/', views.cancel_job, name='cancel_job'),
    path('jobs/<int:job_id>/retry/', views.retry_job, name='retry_job'),
    path('jobs/clear/', views.clear_jobs, name='clear_jobs'),
    path('games/', views.games_list, name='games_list'),
    path('games/rows/', views.games_rows, name='games_rows'),
    path('games/no-polls/', views.games_missing, name='games_missing'),
    path('games/<str:bgg_id>/', views.game_detail, name='game_detail'),
    path('export.csv', views.export_csv, name='export_csv'),
]
