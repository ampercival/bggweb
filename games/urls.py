from django.urls import path
from . import views


urlpatterns = [
    path('', views.home, name='home'),
    path('refresh/', views.refresh, name='refresh'),
    path('jobs/<int:job_id>/', views.job_detail, name='job_detail'),
    path('jobs/clear/', views.clear_jobs, name='clear_jobs'),
    path('games/', views.games_list, name='games_list'),
    path('games/rows/', views.games_rows, name='games_rows'),
    path('games/<str:bgg_id>/', views.game_detail, name='game_detail'),
    path('export.csv', views.export_csv, name='export_csv'),
]
