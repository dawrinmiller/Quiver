import logging
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import EmailMessage
from django.core.validators import URLValidator, validate_email
from django.db.models import F, Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from .models import EmailReminder, Event


logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=15)
CLAIM_TIMEOUT = timedelta(minutes=15)


# Check the website address before adding it to reminder emails.
def site_url():
    value = settings.SITE_URL.strip().rstrip('/')
    try:
        URLValidator(schemes=['http', 'https'])(value)
        parts = urlsplit(value)
        if parts.username or parts.password or parts.query or parts.fragment or parts.path:
            raise ValidationError('Use an origin only.')
        if not settings.DEBUG and parts.scheme != 'https':
            raise ValidationError('Production links require HTTPS.')
    except (ValidationError, ValueError):
        raise ImproperlyConfigured('SITE_URL must be an HTTP(S) origin without credentials, path, query, or fragment; use HTTPS in production.')
    return value


# Find opted-in users with events in the next 24 hours.
def eligible_events(now):
    return Event.objects.filter(
        event_date__gt=now,
        event_date__lte=now + timedelta(hours=24),
        application__user__is_active=True,
        application__user__profile__email_notifications_enabled=True,
    ).select_related('application__user')


# Check whether the account has a valid email address.
def usable_email(value):
    try:
        validate_email(value)
    except ValidationError:
        return False
    return True


# Reserve a reminder so only one sender can use it at a time.
def claim_reminder(reminder, now):
    token = uuid4().hex
    available = (
        Q(status=EmailReminder.Status.PENDING)
        | Q(status=EmailReminder.Status.FAILED, next_attempt_at__lte=now)
        | Q(
            status=EmailReminder.Status.SENDING,
            claimed_at__lte=now - CLAIM_TIMEOUT
        )
    )
    claimed = EmailReminder.objects.filter(
        available,
        pk=reminder.pk,
        attempts__lt=MAX_ATTEMPTS,
    ).update(
        status=EmailReminder.Status.SENDING,
        claim_token=token,
        claimed_at=now,
        attempts=F('attempts') + 1
    )
    return token if claimed else None


# Send one reminder and save its delivery result.
def send_event_reminder(event_id, base_url):
    now = timezone.now()
    event = eligible_events(now).filter(pk=event_id).first()
    if event is None or not usable_email(event.application.user.email):
        return 'skipped'

    # Reuse the delivery record for this version of the event schedule.
    reminder, _ = EmailReminder.objects.get_or_create(
        event=event,
        schedule_version=event.schedule_version,
        defaults={
            'user': event.application.user,
            'scheduled_for': event.event_date
        },
    )
    token = claim_reminder(reminder, now)
    if token is None:
        return 'skipped'
    owned_claim = EmailReminder.objects.filter(
        pk=reminder.pk,
        claim_token=token,
        status='sending'
    )

    # Check again in case the event or account settings changed.
    event = eligible_events(timezone.now()).filter(
        pk=event_id,
        schedule_version=reminder.schedule_version,
        event_date=reminder.scheduled_for,
    ).first()
    if event is None or not usable_email(event.application.user.email):
        owned_claim.update(
            status='pending',
            claim_token='',
            claimed_at=None,
            attempts=F('attempts') - 1
        )
        return 'skipped'
    user = event.application.user
    # Format the event time using the website's configured timezone.
    local_date = timezone.localtime(
        event.event_date,
        timezone.get_default_timezone()
    )
    context = {
        'name': user.get_full_name() or user.get_username(),
        'company': event.application.company,
        'role': event.application.role,
        'event_type': event.get_event_type_display(),
        'event_date': local_date.strftime('%B %d, %Y'),
        'event_time': local_date.strftime('%I:%M %p %Z'),
        'application_url': base_url + reverse(
            'application_detail',
            args=[event.application_id]
        ),
    }
    try:
        # Confirm that this sender still owns the reminder before sending it.
        if not owned_claim.update(claimed_at=timezone.now()):
            return 'skipped'
        # Build the email using event details, without private notes or documents.
        subject = ''.join(
            render_to_string(
                'tracker/email_reminder_subject.txt',
                context
            ).splitlines()
        )
        body = render_to_string('tracker/email_reminder_body.txt', context)
        accepted = EmailMessage(
            subject,
            body,
            settings.DEFAULT_FROM_EMAIL,
            [user.email]
        ).send()
        if accepted != 1:
            raise RuntimeError('Email backend did not accept the message.')
    except Exception:
        # Save a simple failure message and wait before trying again.
        owned_claim.update(
            status='failed',
            last_error='Email delivery failed',
            next_attempt_at=timezone.now() + RETRY_DELAY,
            claim_token=''
        )
        logger.warning('Event reminder delivery failed; inspect delivery status and email configuration.')
        return 'failed'
    # Record success only after the email service accepts the message.
    owned_claim.update(
        status='sent',
        sent_at=timezone.now(),
        last_error='',
        next_attempt_at=None,
        claim_token=''
    )
    return 'sent'


# Mark interrupted reminders as failed once they have used all their attempts.
def expire_exhausted_claims(now):
    return EmailReminder.objects.filter(
        status='sending',
        attempts__gte=MAX_ATTEMPTS,
        claimed_at__lte=now - CLAIM_TIMEOUT,
    ).update(
        status='failed',
        last_error='Delivery outcome unknown after interrupted send',
        claim_token=''
    )
