from datetime import datetime, time, timedelta

from django.utils import timezone

from .models import Event, Notification


# Include imminent events today as well as the existing tomorrow reminders.
def create_event_notifications(user):
    """Remind users about future events today or tomorrow in the app's timezone."""
    now = timezone.now()
    today = timezone.localtime(now).date()
    tomorrow = today + timedelta(days=1)
    end = timezone.make_aware(datetime.combine(tomorrow + timedelta(days=1), time.min))
    events = Event.objects.filter(
        application__user=user,
        event_date__gt=now,
        event_date__lt=end
    ).select_related('application')

    for event in events:
        application = event.application
        event_time = timezone.localtime(event.event_date).strftime('%I:%M %p').lstrip('0')
        day = 'Today' if timezone.localtime(event.event_date).date() == today else 'Tomorrow'
        title = f'{event.get_event_type_display()} {day}'
        message = (
            f'Your {event.get_event_type_display().lower()} for {application.role} '
            f'at {application.company} is {day.lower()} at {event_time}.'
        )
        notification, created = Notification.objects.get_or_create(
            event=event,
            defaults={
                'user': user,
                'title': title,
                'message': message,
            }
        )
        # Refresh Tomorrow to Today after midnight without resetting the read flag.
        if not created and (
            notification.title != title or notification.message != message
        ):
            notification.title = title
            notification.message = message
            notification.save(update_fields=['title', 'message'])


# Provide each signed-in page with the user's unread notification count.
def notification_context(request):
    if not request.user.is_authenticated:
        return {'unread_notification_count': 0}
    create_event_notifications(request.user)
    return {
        'unread_notification_count': Notification.objects.filter(
            user=request.user,
            is_read=False
        ).count()
    }
