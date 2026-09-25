from django.urls import path
from . import views


urlpatterns = [
    path('', views.login_view, name='login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('applications/new/', views.new_application_view, name='new_application'),
    path('applications/tracker/', views.application_tracker_view, name='application_tracker'),
    path('profile/', views.profile_view, name='profile'),
]