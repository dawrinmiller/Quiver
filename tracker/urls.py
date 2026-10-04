from django.urls import path
from . import views
from . import recovery
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy


# Connect each Quiver web address to the page or action it should open.
urlpatterns = [
    path('', views.login_view, name='login'),
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('applications/new/', views.new_application_view, name='new_application'),
    path('applications/tracker/', views.application_tracker_view, name='application_tracker'),
    path('applications/<int:pk>/', views.application_tracker_view, name='application_detail'),
    path('applications/<int:pk>/edit/', views.application_update_view, name='application_update'),
    path('applications/<int:pk>/delete/', views.application_delete_view, name='application_delete'),
    path('applications/<int:application_pk>/events/new/', views.event_form_view, name='event_create'),
    path('applications/<int:application_pk>/events/<int:pk>/edit/', views.event_form_view, name='event_update'),
    path('applications/<int:application_pk>/events/<int:pk>/delete/', views.event_delete_view, name='event_delete'),
    path('events/', views.upcoming_events_view, name='upcoming_events'),
    path('notifications/', views.notifications_view, name='notifications'),
    path('notifications/<int:pk>/read/', views.notification_read_view, name='notification_read'),
    path('notifications/read-all/', views.notifications_read_all_view, name='notifications_read_all'),
    path('password/change/', auth_views.PasswordChangeView.as_view(template_name='tracker/password_change_form.html', success_url=reverse_lazy('password_change_done')), name='password_change'),
    path('password/change/done/', auth_views.PasswordChangeDoneView.as_view(template_name='tracker/password_change_done.html'), name='password_change_done'),
    path('password/reset/', recovery.password_reset_view, name='password_reset'),
    path('password/reset/question/', recovery.password_recovery_question_view, name='password_recovery_question'),
    path('password/reset/new/', recovery.password_reset_confirm_view, name='password_reset_confirm'),
    path('profile/', views.profile_view, name='profile'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
]
