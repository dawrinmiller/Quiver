from django.shortcuts import render


def login_view(request):
    return render(request, 'tracker/login.html')


def dashboard_view(request):
    return render(request, 'tracker/dashboard.html')


def new_application_view(request):
    return render(request, 'tracker/new_application.html')


def application_tracker_view(request):
    return render(request, 'tracker/application_tracker.html')


def profile_view(request):
    return render(request, 'tracker/profile.html')
