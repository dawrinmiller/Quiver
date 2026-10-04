from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Min
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.debug import sensitive_post_parameters
from django.db import transaction

from .forms import ApplicationForm, EventForm, RegistrationForm, ProfileEmailForm, SecurityQuestionForm
from .models import Application, Event, Notification, SecurityQuestion
from .notifications import create_event_notifications

# Handle visits to the login page.
def login_view(request):

    # Check whether someone submitted the login form.
    if request.method == 'POST':

        # Read the username and password entered on the form.
        username = request.POST.get('username')
        password = request.POST.get('password')

        # Check whether the login details match an account.
        user = authenticate(
            request,
            username=username,
            password=password
        )

        # Sign the person in and open their dashboard when the details are correct.
        if user is not None:
            login(request, user)
            return redirect('dashboard')

        # Show an error when the login details are incorrect.
        return render(
            request,
            'tracker/login.html',
            {'error': 'Invalid username or password.'}
        )

    # Show the login form before any details are submitted.
    return render(request, 'tracker/login.html')

# Show the user's applications, totals, search results, and next event dates.
@login_required
def dashboard_view(request):
    applications = Application.objects.filter(user=request.user)
    counts = applications.aggregate(
        applied_count=Count('pk', filter=Q(status='applied')),
        interviewing_count=Count('pk', filter=Q(status='interviewing')),
        offers_count=Count('pk', filter=Q(status='offer')),
        rejected_count=Count('pk', filter=Q(status='rejected')),
    )
    search = request.GET.get('q', '').strip()
    if search:
        applications = applications.filter(Q(company__icontains=search) | Q(role__icontains=search))
    applications = applications.annotate(next_step_date=Min('events__event_date', filter=Q(events__event_date__gte=timezone.now())))
    return render(request, 'tracker/dashboard.html', {**counts, 'applications': applications, 'search': search})

# Save a new application under the signed-in user's account.
@login_required
def new_application_view(request):
    form = ApplicationForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        application = form.save(commit=False)
        application.user = request.user
        application.save()
        return redirect('application_detail', pk=application.pk)
    return render(request, 'tracker/new_application.html', {'form': form})

# Show one application and its dates only to its owner.
@login_required
def application_tracker_view(request, pk=None):
    if pk is None:
        return redirect('dashboard')
    application = get_object_or_404(Application, pk=pk, user=request.user)
    return render(request, 'tracker/application_tracker.html', {
        'application': application,
        'upcoming_events': application.events.filter(event_date__gte=timezone.now()),
        'past_events': application.events.filter(event_date__lt=timezone.now()),
    })


# Let the owner update a saved application.
@login_required
def application_update_view(request, pk):
    application = get_object_or_404(Application, pk=pk, user=request.user)
    form = ApplicationForm(request.POST if request.method == 'POST' else None, instance=application)
    if request.method == 'POST' and form.is_valid():
        form.save()
        return redirect('application_detail', pk=pk)
    return render(request, 'tracker/new_application.html', {'form': form, 'application': application})


# Delete an application only when its owner submits the delete request.
@login_required
@require_POST
def application_delete_view(request, pk):
    get_object_or_404(Application, pk=pk, user=request.user).delete()
    return redirect('dashboard')


# Add or update an event for an application owned by the signed-in user.
@login_required
def event_form_view(request, application_pk, pk=None):
    application = get_object_or_404(Application, pk=application_pk, user=request.user)
    event = get_object_or_404(Event, pk=pk, application=application) if pk else None
    form = EventForm(request.POST if request.method == 'POST' else None, instance=event)
    if request.method == 'POST' and form.is_valid():
        event = form.save(commit=False)
        event.application = application
        event.save()
        # Recreate reminders from the edited event when its owner next opens a page.
        Notification.objects.filter(event=event, user=request.user).delete()
        return redirect('application_detail', pk=application.pk)
    return render(request, 'tracker/event_form.html', {'form': form, 'application': application, 'event': event})


# Delete an event only when its owner submits the delete request.
@login_required
@require_POST
def event_delete_view(request, application_pk, pk):
    event = get_object_or_404(Event, pk=pk, application_id=application_pk, application__user=request.user)
    event.delete()
    return redirect('application_detail', pk=application_pk)


# Show the signed-in user's upcoming events.
@login_required
def upcoming_events_view(request):
    events = Event.objects.filter(application__user=request.user, event_date__gte=timezone.now()).select_related('application')
    return render(request, 'tracker/upcoming_events.html', {'events': events})

# Let the user update their email or security question.
@login_required
@sensitive_post_parameters('current_password', 'security_answer')
def profile_view(request):
    security_post = request.method == 'POST' and request.POST.get('action') == 'security'
    form = ProfileEmailForm(request.POST if request.method == 'POST' and not security_post else None, instance=request.user)
    security = SecurityQuestion.objects.filter(user=request.user).first()
    security_form = SecurityQuestionForm(request.POST if security_post else None, user=request.user,
                                         initial={'security_question': security.question if security else ''})
    if security_post and security_form.is_valid():
        security = security or SecurityQuestion(user=request.user)
        security.question = security_form.cleaned_data['security_question']
        security.set_answer(security_form.cleaned_data['security_answer'])
        security.failed_attempts = 0
        security.locked_until = None
        security.save()
        messages.success(request, 'Security question updated.')
        return redirect('profile')
    if request.method == 'POST' and not security_post and form.is_valid():
        form.save()
        messages.success(request, 'Email address updated.')
        return redirect('profile')
    return render(request, 'tracker/profile.html', {'form': form, 'security_form': security_form, 'security_configured': security is not None})


# Show only the signed-in user's notifications.
@login_required
def notifications_view(request):
    create_event_notifications(request.user)
    notifications = Notification.objects.filter(user=request.user).select_related('event__application')
    return render(request, 'tracker/notifications.html', {'notifications': notifications})


# Mark one of the user's notifications as read.
@login_required
@require_POST
def notification_read_view(request, pk):
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    notification.is_read = True
    notification.save(update_fields=['is_read'])
    return redirect('notifications')


# Mark all of the user's notifications as read.
@login_required
@require_POST
def notifications_read_all_view(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return redirect('notifications')


# Handle account registration and keep private answers out of error reports.
@sensitive_post_parameters('password1', 'password2', 'security_answer')
@transaction.atomic
def register_view(request):
    if request.method == 'POST':
        form = RegistrationForm(request.POST)

        # Create the account when the registration details are valid.
        if form.is_valid():
            form.save()
            return redirect('login')

    # Prepare an empty registration form for a new visitor.
    else:
        form = RegistrationForm()

    # Show the registration page and any form errors.
    return render(
        request,
        'tracker/register.html',
        {'form': form}
    )


# Sign the user out and return to the login page.
@login_required
@require_POST
def logout_view(request):
    logout(request)
    return redirect('login')
