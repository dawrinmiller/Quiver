from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from smtplib import SMTPDataError, SMTPServerDisconnected
from threading import Barrier
from time import sleep
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.db import close_old_connections, OperationalError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import EventForm
from .models import Application, Event, InterviewThankYouEmail
from .thank_you import initial_message, claim_draft


# Check interview contacts and thank-you emails without sending real email.
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ThankYouTests(TestCase):
    # Create the sample account and records used by these tests.
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'candidate',
            first_name='Alex',
            last_name='Lee'
        )
        self.application = Application.objects.create(
            user=self.user,
            company='Example & Co',
            role='Engineer',
            notes='SECRET NOTES'
        )
        self.event = Event.objects.create(
            application=self.application,
            event_type='interview',
            event_date=timezone.now()-timedelta(hours=2),
            interviewer_name='Sarah',
            interviewer_email='sarah@example.com'
        )
        self.client.force_login(self.user)
        self.url = reverse('interview_thank_you', args=[self.event.pk])

    # Open the email form and return a sample edited message.
    def draft(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        return {
            'token': str(response.context['form'].initial['token']),
            'subject': 'Edited subject',
            'body': 'My edited message',
            'confirm_send': 'on'
        }

    # Check optional contacts and validation.
    def test_optional_contacts_and_validation(self):
        data = {
            'event_type': 'interview',
            'event_date': timezone.now()+timedelta(days=2)
        }
        self.assertTrue(EventForm(data).is_valid())
        for email, valid in [('person@example.com', True), ('invalid', False)]:
            self.assertEqual(
                EventForm({**data, 'interviewer_email': email}).is_valid(),
                valid
            )
        self.assertFalse(
            EventForm(
                {
                    **data,
                    'event_type': 'other',
                    'interviewer_name': 'Sarah'
                }
            ).is_valid()
        )
        self.assertFalse(
            EventForm(
                {
                    **data,
                    'event_type': 'deadline',
                    'interviewer_email': 'p@example.com'
                }
            ).is_valid()
        )

    # Check edit and clear contacts.
    def test_edit_and_clear_contacts(self):
        data = {
            'event_type': 'interview',
            'event_date': self.event.event_date,
            'interviewer_email': 'new@example.com'
        }
        form = EventForm(data, instance=self.event)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.event.refresh_from_db()
        self.assertEqual(self.event.interviewer_email, 'new@example.com')
        data.update(event_type='other', confirm_clear_contacts='on')
        form = EventForm(data, instance=self.event)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.event.refresh_from_db()
        self.assertEqual(self.event.interviewer_email, '')
        self.assertEqual(self.event.interviewer_name, '')

    # Check clear requires confirmation.
    def test_clear_requires_confirmation(self):
        form = EventForm(
            {'event_type': 'other', 'event_date': self.event.event_date},
            instance=self.event
        )
        self.assertFalse(form.is_valid())
        self.assertIn('confirm_clear_contacts', form.errors)

    # Check eligibility.
    def test_eligibility(self):
        for field, value in [
            ('event_type', 'other'),
            ('interviewer_email', ''),
            ('event_date', timezone.now()+timedelta(days=1))
        ]:
            original = getattr(self.event, field)
            setattr(self.event, field, value)
            self.event.save()
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, {}).status_code, 403)
            setattr(self.event, field, original)
            self.event.save()
        self.assertEqual(self.client.get(self.url).status_code, 200)

    # Check date only conservative rule.
    def test_date_only_conservative_rule(self):
        self.event.event_date = timezone.localdate()
        self.assertFalse(self.event.interview_completed)
        self.event.event_date -= timedelta(days=1)
        self.assertTrue(self.event.interview_completed)

    # Check template and get cancel do not send.
    def test_template_and_get_cancel_do_not_send(self):
        data = self.draft()
        message = self.client.get(self.url).context['form'].initial
        for text in ['Sarah', 'Example & Co', 'Engineer', 'Alex Lee']:
            self.assertIn(text, message['body'])
        self.assertNotIn('SECRET NOTES', message['body'])
        self.event.interviewer_name = ''
        self.user.first_name = self.user.last_name = ''
        subject, body = initial_message(self.event, self.user)
        self.assertTrue(body.startswith('Hello,'))
        self.assertIn('candidate', body)
        self.client.get(reverse('application_detail', args=[self.application.pk]))
        self.assertEqual(len(mail.outbox), 0)

    # Check send snapshot sender recipient and duplicate.
    def test_send_snapshot_sender_recipient_and_duplicate(self):
        data = self.draft()
        data['recipient'] = 'attacker@example.com'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.client.post(self.url, data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['sarah@example.com'])
        self.assertEqual(mail.outbox[0].from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(mail.outbox[0].subject, 'Edited subject')
        self.assertEqual(mail.outbox[0].body, 'My edited message')
        record = InterviewThankYouEmail.objects.get(token=data['token'])
        self.assertEqual(record.status, 'sent')
        self.assertIsNotNone(record.sent_at)
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, 'applied')
        response = self.client.get(
            reverse(
                'application_detail',
                args=[self.application.pk]
            )
        )
        self.assertContains(response, 'Thank-You Email Sent')
        self.assertContains(response, 'Send Another Email')

    # Check confirmation and validation.
    def test_confirmation_and_validation(self):
        data = self.draft()
        for changes in [
            {'confirm_send': ''},
            {'subject': 'Header\r\nBcc: x@example.com'},
            {'body': ''},
            {'body': 'x'*10001},
            {'subject': 'x'*201}
        ]:
            response = self.client.post(self.url, {**data, **changes})
            self.assertTrue(response.context['form'].errors)
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(self.url, data)
        another = self.draft()
        self.client.post(self.url, another)
        self.assertEqual(len(mail.outbox), 1)
        self.client.post(self.url, {**another, 'confirm_another': 'on'})
        self.assertEqual(len(mail.outbox), 2)

    # Check recipient change requires new review.
    def test_recipient_change_requires_new_review(self):
        data = self.draft()
        self.event.interviewer_email = 'changed@example.com'
        self.event.save()
        response = self.client.post(self.url, data)
        self.assertContains(response, 'recipient has changed')
        self.assertEqual(len(mail.outbox), 0)

    # Check definite failure preserves draft and retry.
    def test_definite_failure_preserves_draft_and_retry(self):
        data = self.draft()
        with patch(
            'tracker.thank_you.EmailMessage.send',
            side_effect=SMTPDataError(550, b'Resend testing requires verified domain')
        ):
            response = self.client.post(self.url, data)
        self.assertContains(response, 'verified sending domain')
        self.assertContains(response, 'My edited message')
        record = InterviewThankYouEmail.objects.get(token=data['token'])
        self.assertEqual(record.status, 'failed')
        self.assertIsNone(record.sent_at)
        retry = {**data, 'token': str(response.context['form']['token'].value())}
        self.client.post(self.url, retry)
        self.assertEqual(len(mail.outbox), 1)

    # Check uncertain failure cannot retry same token.
    def test_uncertain_failure_cannot_retry_same_token(self):
        data = self.draft()
        with patch(
            'tracker.thank_you.EmailMessage.send',
            side_effect=SMTPServerDisconnected('lost')
        ):
            response = self.client.post(self.url, data)
        self.assertContains(response, 'Delivery could not be confirmed')
        self.assertEqual(
            InterviewThankYouEmail.objects.get(token=data['token']).status,
            'unknown'
        )
        self.client.post(self.url, data)
        self.assertEqual(len(mail.outbox), 0)
        another = self.draft()
        self.client.post(self.url, another)
        self.assertEqual(len(mail.outbox), 0)

    # Check limit and zero accepted.
    def test_limit_and_zero_accepted(self):
        data = self.draft()
        with patch('tracker.thank_you.EmailMessage.send', return_value=0):
            self.client.post(self.url, data)
        self.assertEqual(
            InterviewThankYouEmail.objects.get(token=data['token']).status,
            'failed'
        )
        for i in range(9):
            InterviewThankYouEmail.objects.create(
                event=self.event,
                user=self.user,
                token=uuid4(),
                recipient='sarah@example.com',
                subject='s',
                body='b',
                status='failed',
                attempted_at=timezone.now()
            )
        response = self.client.post(self.url, self.draft())
        self.assertContains(response, 'Sending limit reached')
        self.assertEqual(len(mail.outbox), 0)

    # Check privacy and csrf.
    def test_privacy_and_csrf(self):
        data = self.draft()
        other = get_user_model().objects.create_user('other')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.post(self.url, data).status_code, 404)
        self.assertEqual(
            self.client.get(
                reverse(
                    'application_detail',
                    args=[self.application.pk]
                )
            ).status_code,
            404
        )
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.url, data).status_code, 403)

    # Check calendar contacts and tracker.
    def test_calendar_contacts_and_tracker(self):
        local = timezone.localtime(self.event.event_date)
        response = self.client.get(
            reverse('job_search_calendar'),
            {'year': local.year, 'month': local.month, 'day': local.day}
        )
        self.assertContains(response, 'sarah@example.com')
        self.assertContains(response, self.url)
        other = get_user_model().objects.create_user('other')
        self.client.force_login(other)
        self.assertNotContains(
            self.client.get(
                reverse('job_search_calendar'),
                {'year': local.year, 'month': local.month, 'day': local.day}
            ),
            'sarah@example.com'
        )

    # Check event routes save optional contacts.
    def test_event_routes_save_optional_contacts(self):
        date = timezone.now()+timedelta(days=3)
        response = self.client.post(
            reverse('event_create', args=[self.application.pk]),
            {
                'event_type': 'interview',
                'event_date': date,
                'interviewer_email': 'test@example.com'
            }
        )
        self.assertEqual(response.status_code, 302)
        event = self.application.events.latest('pk')
        response = self.client.post(
            reverse('event_update', args=[self.application.pk, event.pk]),
            {
                'event_type': 'interview',
                'event_date': date,
                'interviewer_name': '',
                'interviewer_email': ''
            }
        )
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.interviewer_email, '')


# Exercise separate connections against SQLite instead of relying on row locks.
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ConcurrentThankYouTests(TransactionTestCase):
    # Check overlapping submissions claim once.
    def test_overlapping_submissions_claim_once(self):
        user = get_user_model().objects.create_user('concurrent')
        app = Application.objects.create(user=user, company='Co', role='Engineer')
        event = Event.objects.create(
            application=app,
            event_type='interview',
            event_date=timezone.now()-timedelta(days=1),
            interviewer_email='person@example.com'
        )
        url = reverse('interview_thank_you', args=[event.pk])
        drafts = [
            InterviewThankYouEmail.objects.create(
                event=event,
                user=user,
                token=uuid4(),
                recipient=event.interviewer_email,
                subject='Thanks',
                body='Thank you'
            ) for _ in range(2)
        ]
        barrier = Barrier(2)
        # Try to reserve a draft from a separate database connection.
        def claim(index):
            close_old_connections()
            barrier.wait(timeout=10)
            try:
                # Shared in-memory SQLite immediately rejects table locks rather
                # than using the on-disk database's busy timeout.
                for _ in range(30):
                    try:
                        return claim_draft(
                            event,
                            drafts[index],
                            user,
                            {
                                'subject': 'Thanks',
                                'body': 'Thank you',
                                'confirm_another': True
                            }
                        )
                    except OperationalError:
                        sleep(0.01)
                self.fail('SQLite remained locked')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, [0, 1]))
        self.assertEqual(results.count(None), 1)
        self.assertEqual(
            InterviewThankYouEmail.objects.filter(status='sending').count(),
            1
        )
