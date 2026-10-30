import calendar
from datetime import date, datetime, time
from urllib.parse import urlencode

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Min, Q
from django.forms.models import model_to_dict
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ApplicationForm
from .models import Application, Event


# Check calendar dates before using them in a query.
class CalendarMonthForm(forms.Form):
    year = forms.IntegerField(min_value=1900, max_value=2100)
    month = forms.IntegerField(min_value=1, max_value=12)
    day = forms.IntegerField(min_value=1, max_value=31, required=False)

    # Reject dates such as February 30 instead of guessing a different day.
    def clean(self):
        data = super().clean()
        if 'year' in data and 'month' in data:
            try:
                date(data['year'], data['month'], data.get('day') or 1)
            except ValueError:
                self.add_error('day', 'Choose a day that exists in this month.')
        return data


# Offer only applications owned by the person adding the event.
class CalendarApplicationForm(forms.Form):
    application = forms.ModelChoiceField(queryset=Application.objects.none())
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))

    # Limit the application choices to the signed-in user.
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['application'].queryset = Application.objects.filter(user=user)


# Return to the selected day using a local URL built by Django.
def calendar_url(day):
    # Use the local calendar day when the event includes a time.
    if isinstance(day, datetime):
        day = timezone.localtime(day).date()
    query = urlencode({
        'year': day.year,
        'month': day.month,
        'day': day.day
    })
    return reverse('job_search_calendar') + '?' + query


# Group the user's applications using the model's existing status choices.
@login_required
def application_board_view(request):
    # Load the applications and the next event date in one query.
    applications = list(
        Application.objects.filter(user=request.user).annotate(
            next_step_date=Min(
                'events__event_date',
                filter=Q(events__event_date__gte=timezone.now())
            )
        )
    )
    # Place each application in its matching status column.
    columns = [
        {
            'value': value,
            'label': label,
            'applications': [
                application
                for application in applications
                if application.status == value
            ]
        }
        for value, label in Application.Status.choices
    ]
    return render(
        request,
        'tracker/application_board.html',
        {
            'columns': columns,
            'status_choices': Application.Status.choices,
            'has_applications': bool(applications),
            'today': timezone.localdate()
        }
    )


# Update only the owned application's status and explicitly supplied date.
@login_required
@require_POST
def application_status_view(request, pk):
    # Save the whole update together and check who owns the application.
    with transaction.atomic():
        application = get_object_or_404(
            Application.objects.select_for_update(),
            pk=pk,
            user=request.user
        )

        # Validate against the same form used by the existing edit page.
        data = model_to_dict(application, fields=ApplicationForm.Meta.fields)
        data['status'] = request.POST.get('status', '')
        if 'date_submitted' in request.POST:
            data['date_submitted'] = request.POST['date_submitted']
        form = ApplicationForm(data, instance=application, user=request.user)
        # Send clear field errors back to the board without saving.
        if not form.is_valid():
            return JsonResponse(
                {
                    'success': False,
                    'errors': form.errors.get_json_data()
                },
                status=400
            )

        # Keep the existing timestamp and interview-history behavior.
        application = form.save(commit=False)
        application.save(update_fields=['status', 'date_submitted', 'updated_at'])
        return JsonResponse({
            'success': True,
            'status': application.status,
            'label': application.get_status_display(),
            'date_submitted': (
                application.date_submitted.isoformat()
                if application.date_submitted else ''
            )
        })


# Build a monthly calendar from existing events in the application timezone.
@login_required
def job_search_calendar_view(request):
    # Read the requested month, using the current local month by default.
    today = timezone.localdate()
    values = request.GET.copy()
    values.setdefault('year', str(today.year))
    values.setdefault('month', str(today.month))
    month_form = CalendarMonthForm(values)
    # Show a helpful page when the requested date is invalid.
    if not month_form.is_valid():
        return render(
            request,
            'tracker/calendar_invalid.html',
            {'form': month_form},
            status=400
        )
    year = month_form.cleaned_data['year']
    month = month_form.cleaned_data['month']
    # Choose the highlighted day and the neighboring months.
    selected_number = month_form.cleaned_data.get('day')
    if not selected_number:
        selected_number = (
            today.day
            if (year, month) == (today.year, today.month)
            else 1
        )
    selected = date(year, month, selected_number)
    first = date(year, month, 1)
    previous = (
        date(year - 1, 12, 1)
        if month == 1
        else date(year, month - 1, 1)
    )
    following = (
        date(year + 1, 1, 1)
        if month == 12
        else date(year, month + 1, 1)
    )

    # Limit the event query to this month, including local midnight boundaries.
    start = timezone.make_aware(datetime.combine(first, time.min))
    end = timezone.make_aware(datetime.combine(following, time.min))
    events = list(
        Event.objects.filter(
            application__user=request.user,
            event_date__gte=start,
            event_date__lt=end
        ).select_related('application')
    )
    # Group the events by their local calendar date.
    events_by_day = {}
    for event in events:
        local_day = timezone.localtime(event.event_date).date()
        events_by_day.setdefault(local_day, []).append(event)

    # Keep a compact month grid and show the full list below the selected day.
    weeks = []
    for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, month):
        cells = []
        for number in week:
            # Leave empty spaces before and after the dates in this month.
            if not number:
                cells.append(None)
                continue
            # Keep two event previews and count any remaining events.
            day = date(year, month, number)
            day_events = events_by_day.get(day, [])
            cells.append({
                'date': day,
                'url': calendar_url(day),
                'events': day_events[:2],
                'count': len(day_events),
                'more': max(0, len(day_events) - 2),
                'selected': day == selected,
                'today': day == today
            })
        weeks.append(cells)
    return render(
        request,
        'tracker/job_search_calendar.html',
        {
            'month': first,
            'previous_url': calendar_url(previous) if previous.year >= 1900 else '',
            'next_url': calendar_url(following) if following.year <= 2100 else '',
            'weeks': weeks,
            'weekdays': ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
            'selected_day': selected,
            'selected_events': events_by_day.get(selected, []),
            'has_events': bool(events)
        }
    )


# Choose an application, then use the existing event creation form.
@login_required
def calendar_event_create_view(request):
    form = CalendarApplicationForm(
        request.GET if 'application' in request.GET else None,
        user=request.user,
        initial={'date': request.GET.get('date', timezone.localdate().isoformat())}
    )
    # Open the existing event form after an application has been selected.
    if form.is_bound and form.is_valid():
        url = reverse('event_create', args=[form.cleaned_data['application'].pk])
        query = urlencode({
            'from_calendar': '1',
            'calendar_date': form.cleaned_data['date'].isoformat()
        })
        return redirect(url + '?' + query)
    return render(request, 'tracker/calendar_event_create.html', {'form': form})
