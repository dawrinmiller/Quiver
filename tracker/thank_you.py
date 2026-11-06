import logging
import smtplib
from datetime import timedelta
from uuid import uuid4

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .forms import ThankYouForm
from .models import Event, InterviewThankYouEmail

logger = logging.getLogger(__name__)


# Write a starting email using the interview and account details.
def initial_message(event, user):
    greeting = f'Hi {event.interviewer_name},' if event.interviewer_name else 'Hello,'
    company, role = event.application.company, event.application.role
    subject = f'Thank You for the Interview - {role}'
    body = (
        f'{greeting}\n\nThank you for taking the time to speak with me about the {role} '
        f'position at {company}. I appreciated the opportunity to learn more about the role '
        f'and your team.\n\nI remain very interested in the opportunity and the possibility '
        f'of contributing to {company}.\n\nThank you again for your time and consideration. '
        'I look forward to hearing about the next steps in the hiring process.\n\n'
        f'Best regards,\n{user.get_full_name().strip() or user.get_username()}'
    )
    return subject.replace('\r', ' ').replace('\n', ' ')[:200], body


# Check whether another email needs an extra confirmation.
def needs_confirmation(event):
    return event.thank_you_emails.filter(status__in=[
        'sent',
        'sending',
        'unknown'
    ]).exists()


# Reserve this draft so two requests cannot send it at the same time.
def claim_draft(event, draft, user, data):
    # Let only one request reserve the email at a time.
    # Release the database before contacting the email provider.
    with transaction.atomic():
        get_user_model().objects.filter(pk=user.pk).update(last_login=F('last_login'))
        event.refresh_from_db()
        draft.refresh_from_db()
        # Stop a request from sending the same draft again.
        if draft.status != 'draft':
            return 'This submission was already processed or is awaiting a delivery result. It will not be sent again.'
        # Stop if the interview details changed after the user reviewed them.
        if not event.thank_you_eligible or draft.recipient != event.interviewer_email:
            return 'The interview or recipient has changed. Open a new composition page and review it again.'
        # Require permission before sending another email for this interview.
        if needs_confirmation(event) and not data['confirm_another']:
            return 'Confirm that you want to send another email.'
        # Limit each user to ten sending attempts in an hour.
        if InterviewThankYouEmail.objects.filter(
            user=user,
            attempted_at__gte=timezone.now()-timedelta(hours=1)
        ).count() >= 10:
            return 'Sending limit reached (10 attempts per hour). Please try later.'
        # Wait if another request is already sending an email.
        if event.thank_you_emails.filter(status='sending').exists():
            return 'Another email is being sent for this interview. Please wait and check its status.'
        # Save the exact message that the user reviewed.
        draft.subject = data['subject']
        draft.body = data['body']
        draft.status = 'sending'
        draft.attempted_at = timezone.now()
        draft.failure_info = ''
        draft.save()
    return None


# Let the owner review the email before choosing to send it.
@login_required
@require_http_methods(['GET', 'POST'])
def compose(request, pk):
    event = get_object_or_404(
        Event.objects.select_related('application'),
        pk=pk,
        application__user=request.user
    )
    # Only completed interviews with a saved email can use this page.
    if not event.thank_you_eligible:
        return HttpResponse(
            'This event is not a completed interview with a saved recipient.',
            status=403
        )
    # Check the saved recipient address before showing the form.
    try:
        validate_email(event.interviewer_email)
    except ValidationError:
        return HttpResponse(
            'Edit the event to save a valid interviewer email.',
            status=400
        )
    repeat = needs_confirmation(event)
    # Opening the page creates an editable draft without sending it.
    if request.method == 'GET':
        subject, body = initial_message(event, request.user)
        draft = InterviewThankYouEmail.objects.create(
            event=event,
            user=request.user,
            token=uuid4(),
            recipient=event.interviewer_email,
            subject=subject,
            body=body
        )
        form = ThankYouForm(
            initial={
                'token': draft.token,
                'subject': subject,
                'body': body
            }
        )
    else:
        # Check the reviewed form before reserving and sending its draft.
        form = ThankYouForm(request.POST)
        if form.is_valid():
            draft = get_object_or_404(
                InterviewThankYouEmail,
                token=form.cleaned_data['token'],
                event=event,
                user=request.user
            )
            try:
                error = claim_draft(event, draft, request.user, form.cleaned_data)
                if error:
                    form.add_error(None, error)
                    repeat = needs_confirmation(event)
                if not form.errors:
                    return deliver(request, event, draft, form, repeat)
            except (IntegrityError, OperationalError):
                form.add_error(
                    None,
                    'Another request is being processed. Check the event status before trying again.'
                )
    # Hide the extra confirmation when this is the first email.
    if not repeat:
        form.fields['confirm_another'].widget = forms.HiddenInput()
    # Show the form together with the previous email results.
    return render(
        request,
        'tracker/thank_you_compose.html',
        {
            'event': event,
            'form': form,
            'repeat': repeat,
            'history': event.thank_you_emails.exclude(status='draft').order_by(
                '-created_at',
                '-pk'
            )
        }
    )


# Send the reviewed email and save what happened.
def deliver(request, event, draft, form, repeat):
    # Treat an interrupted connection as uncertain so it is not retried by mistake.
    status, failure = 'unknown', 'Delivery could not be confirmed. Check with the recipient before sending another email.'
    # Send through the email service already configured in Django.
    try:
        accepted = EmailMessage(
            draft.subject,
            draft.body,
            settings.DEFAULT_FROM_EMAIL,
            [draft.recipient]
        ).send(fail_silently=False)
        if accepted == 1:
            status, failure = 'sent', ''
        else:
            status, failure = 'failed', 'The email backend did not accept the email. You may retry your draft.'
    except (
        smtplib.SMTPRecipientsRefused,
        smtplib.SMTPSenderRefused,
        smtplib.SMTPDataError,
        smtplib.SMTPAuthenticationError,
        smtplib.SMTPConnectError,
        ConnectionRefusedError
    ) as exc:
        status, failure = 'failed', 'The email provider rejected this email. Check your email configuration before retrying.'
        # Explain provider restrictions without saving private error details.
        detail = str(exc).lower()
        if any(
            word in detail for word in (
                'resend',
                'verify',
                'verified',
                'testing',
                'own email'
            )
        ):
            failure = (
                'Resend testing senders restrict recipients. A verified sending '
                'domain is needed to email arbitrary recipients; use a permitted test'
                ' recipient until then.'
            )
        logger.warning('Thank-you email %s rejected (%s)', draft.pk, type(exc).__name__)
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        logger.warning(
            'Thank-you email %s outcome uncertain (%s)',
            draft.pk,
            type(exc).__name__
        )
    # Save whether the email was accepted, rejected, or left uncertain.
    try:
        InterviewThankYouEmail.objects.filter(pk=draft.pk, status='sending').update(
            status=status,
            failure_info=failure,
            sent_at=timezone.now() if status == 'sent' else None
        )
    except OperationalError:
        # Keep this attempt blocked if its final result could not be saved.
        form.add_error(
            None,
            'The delivery result could not be saved. Do not retry until you have checked with the recipient.'
        )
    else:
        if status == 'sent':
            messages.success(request, 'Thank-You Email Sent')
            return redirect('application_detail', pk=event.application_id)
        form.add_error(None, failure)
        if status == 'failed':
            # Keep the failed attempt and create a new draft for a safe retry.
            retry = InterviewThankYouEmail.objects.create(
                event=event,
                user=request.user,
                token=uuid4(),
                recipient=draft.recipient,
                subject=draft.subject,
                body=draft.body
            )
            form.data = form.data.copy()
            form.data['token'] = str(retry.token)
    # Show the form together with the previous email results.
    return render(
        request,
        'tracker/thank_you_compose.html',
        {
            'event': event,
            'form': form,
            'repeat': repeat,
            'history': event.thank_you_emails.exclude(status='draft').order_by(
                '-created_at',
                '-pk'
            )
        }
    )

