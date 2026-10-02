from datetime import datetime, time, timedelta

from django.utils import timezone

from .models import Event, Notification


def create_event_notifications(user):
    """Create one reminder per event on the calendar day before it occurs."""
    tomorrow = timezone.localdate() + timedelta(days=1)
    start = timezone.make_aware(datetime.combine(tomorrow, time.min))
    end = timezone.make_aware(datetime.combine(tomorrow + timedelta(days=1), time.min))
    events = Event.objects.filter(application__user=user, event_date__gte=start, event_date__lt=end).select_related('application')
    for event in events:
        application = event.application
        event_time = timezone.localtime(event.event_date).strftime('%I:%M %p').lstrip('0')
        title = f'{event.get_event_type_display()} Tomorrow'
        message = (f'Your {event.get_event_type_display().lower()} for {application.role} '
                   f'at {application.company} is tomorrow at {event_time}.')
        Notification.objects.get_or_create(event=event, defaults={
            'user': user, 'title': title, 'message': message,
        })


def notification_context(request):
    if not request.user.is_authenticated:
        return {'unread_notification_count': 0}
    create_event_notifications(request.user)
    return {'unread_notification_count': Notification.objects.filter(user=request.user, is_read=False).count()}
