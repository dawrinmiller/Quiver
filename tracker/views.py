from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required

def login_view(request):

    if request.method == 'POST':

        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:
            login(request, user)
            return redirect('dashboard')

        return render(
            request,
            'tracker/login.html',
            {'error': 'Invalid username or password.'}
        )

    return render(request, 'tracker/login.html')

@login_required
def dashboard_view(request):
    return render(request, 'tracker/dashboard.html')

@login_required
def new_application_view(request):
    return render(request, 'tracker/new_application.html')

@login_required
def application_tracker_view(request):
    return render(request, 'tracker/application_tracker.html')

@login_required
def profile_view(request):
    return render(request, 'tracker/profile.html')


def register_view(request):
    if request.method == 'POST':
        form = UserCreationForm(request.POST)

        if form.is_valid():
            form.save()
            return redirect('login')

    else:
        form = UserCreationForm()

    return render(
        request,
        'tracker/register.html',
        {'form': form}
    )


def logout_view(request):
    logout(request)
    return redirect('login')