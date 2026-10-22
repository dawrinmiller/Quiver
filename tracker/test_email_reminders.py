from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone as datetime_timezone
from io import StringIO
from smtplib import SMTPException
from threading import Barrier
from time import sleep
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command, CommandError
from django.db import close_old_connections, IntegrityError, OperationalError, transaction
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .email_reminders import claim_reminder, CLAIM_TIMEOUT, MAX_ATTEMPTS, RETRY_DELAY
from .models import Application, EmailReminder, Event, Notification, Profile
from .notifications import create_event_notifications


# Test reminder emails without sending real emails.
@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_URL='http://127.0.0.1:8000',
    DEBUG=True,
    TIME_ZONE='America/New_York'
)
class EmailReminderTests(TestCase):

    # Create the sample data needed for each test.
    def setUp(self):
        self.now = datetime(2026, 10, 22, 16, tzinfo=datetime_timezone.utc)
        self.user = get_user_model().objects.create_user(
            'reminder',
            'reminder@example.com'
        )
        self.profile = Profile.objects.create(
            user=self.user,
            biography='Private biography',
            email_notifications_enabled=True
        )
        self.application = Application.objects.create(
            user=self.user,
            company='Acme & Co',
            role='Engineer',
            notes='Private notes'
        )
        self.event = self.make_event(12)

    # Create a sample event at the requested time.
    def make_event(self, hours, **kwargs):
        return Event.objects.create(
            application=self.application,
            event_type='interview',
            event_date=self.now + timedelta(hours=hours),
            description='Private event description',
            **kwargs
        )

    # Run the reminder check using a fixed test time.
    def run_command(self):
        output = StringIO()
        with patch(
            'tracker.email_reminders.timezone.now',
            return_value=self.now
        ):
            call_command('send_reminders', stdout=output)
        return output.getvalue()

    # Send only within 24 hours and include the correct event details.
    def test_window_boundaries_and_message(self):
        self.make_event(24)
        self.make_event(24.001)
        self.make_event(0)
        self.make_event(-1)
        self.assertIn('Sent: 2', self.run_command())
        self.assertEqual(len(mail.outbox), 2)
        message = mail.outbox[0]
        for text in (
            'reminder',
            'Acme & Co',
            'Engineer',
            'Interview',
            'October 23, 2026',
            '12:00 AM EDT',
            'http://127.0.0.1:8000' + reverse(
                'application_detail',
                args=[self.application.pk]
            )
        ):
            self.assertIn(text, message.body)
        self.assertNotIn('Private', message.body)
        self.assertEqual(message.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(message.to, [self.user.email])
        self.assertIn('Upcoming Interview', message.subject)
        self.assertEqual(Notification.objects.count(), 0)

    # Prevent duplicate emails without changing in-app read status.
    def test_repeated_commands_and_read_status_are_independent(self):
        with patch(
            'tracker.notifications.timezone.now',
            return_value=self.now
        ):
            create_event_notifications(self.user)
        notification = Notification.objects.get(event=self.event)
        notification.is_read = True
        notification.save()
        self.run_command()
        self.run_command()
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        reminder = EmailReminder.objects.get()
        self.assertEqual(reminder.status, 'sent')
        self.assertEqual(reminder.attempts, 1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmailReminder.objects.create(
                user=self.user,
                event=self.event,
                schedule_version=self.event.schedule_version,
                scheduled_for=self.event.event_date
            )

    # Skip disabled reminders, invalid email addresses, and inactive users.
    def test_disabled_missing_invalid_email_and_inactive_users(self):
        for email, active, enabled in [
            (
                '',
                True,
                True
            ),
            (
                'invalid',
                True,
                True
            ),
            (
                self.user.email,
                False,
                True
            ),
            (
                self.user.email,
                True,
                False
            )
        ]:
            self.user.email, self.user.is_active = email, active
            self.user.save()
            self.profile.email_notifications_enabled = enabled
            self.profile.save()
            self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(EmailReminder.objects.count(), 0)

    # Send again for schedule changes, but not for description edits.
    def test_rescheduling_type_changes_and_description_edits(self):
        self.run_command()
        self.event.description = 'Edited notes'
        self.event.save()
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        original_time = self.event.event_date
        self.event.event_date = self.now + timedelta(hours=36)
        self.event.save()
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        # Moving back to a previous time is still a new schedule version.
        self.event.event_date = original_time
        self.event.save()
        self.run_command()
        self.assertEqual(len(mail.outbox), 2)
        self.event.event_type = 'deadline'
        self.event.save(update_fields=['event_type'])
        self.run_command()
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn('Application deadline', mail.outbox[-1].body)

    # Wait before retrying and send to the user's current email address.
    def test_failure_delay_limit_and_successful_retry_use_current_email(self):
        with patch(
            'tracker.email_reminders.EmailMessage.send',
            side_effect=SMTPException('secret recipient')
        ):
            with self.assertLogs(
                'tracker.email_reminders',
                level='WARNING'
            ) as logs:
                self.assertIn('Failed: 1', self.run_command())
        self.assertNotIn('secret recipient', str(logs.output))
        reminder = EmailReminder.objects.get()
        self.assertEqual(reminder.status, 'failed')
        self.assertEqual(reminder.last_error, 'Email delivery failed')
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.now += RETRY_DELAY
        self.profile.email_notifications_enabled = False
        self.profile.save()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.profile.email_notifications_enabled = True
        self.profile.save()
        self.user.email = 'updated@example.com'
        self.user.save()
        self.run_command()
        self.assertEqual(mail.outbox[0].to, ['updated@example.com'])
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, 'sent')
        self.assertEqual(reminder.attempts, 2)

    # Stop after three failed sends and keep in-app reminders working.
    def test_retry_exhaustion_backend_zero_and_inapp_survival(self):
        with patch(
            'tracker.email_reminders.EmailMessage.send',
            return_value=0
        ):
            for _ in range(MAX_ATTEMPTS + 1):
                with self.assertLogs(
                    'tracker.email_reminders',
                    level='WARNING'
                ) if _ < MAX_ATTEMPTS else nullcontext():
                    self.run_command()
                self.now += RETRY_DELAY
        reminder = EmailReminder.objects.get()
        self.assertEqual(reminder.status, 'failed')
        self.assertEqual(reminder.attempts, MAX_ATTEMPTS)
        with patch(
            'tracker.notifications.timezone.now',
            return_value=self.now
        ):
            create_event_notifications(self.user)
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 1)

    # Retry interrupted sends only while attempts remain.
    def test_stale_claim_recovery_and_exhaustion(self):
        reminder = EmailReminder.objects.create(
            user=self.user,
            event=self.event,
            schedule_version=1,
            scheduled_for=self.event.event_date,
            status='sending',
            claimed_at=self.now,
            attempts=1,
            claim_token='old'
        )
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.now += CLAIM_TIMEOUT
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        reminder.refresh_from_db()
        self.assertEqual(reminder.attempts, 2)
        self.assertEqual(reminder.status, 'sent')
        reminder.status, reminder.attempts = 'sending', MAX_ATTEMPTS
        reminder.claimed_at = self.now - CLAIM_TIMEOUT
        reminder.save()
        self.run_command()
        reminder.refresh_from_db()
        self.assertEqual(reminder.status, 'failed')
        self.assertIn('unknown', reminder.last_error)

    # Skip an event that changes or disappears before sending.
    def test_changed_or_deleted_event_after_claim_is_skipped(self):
        original_claim = claim_reminder

        # Move the event after a sender has reserved its reminder.
        def change_after_claim(reminder, now):
            token = original_claim(reminder, now)
            event = Event.objects.get(pk=reminder.event_id)
            event.event_date += timedelta(days=2)
            event.save()
            return token
        with patch(
            'tracker.email_reminders.claim_reminder',
            side_effect=change_after_claim
        ):
            self.run_command()
        self.assertEqual(len(mail.outbox), 0)
        self.event.delete()
        self.run_command()
        self.assertEqual(EmailReminder.objects.count(), 0)
        self.event = self.make_event(12)
        self.application.delete()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    # Make sure opening a page does not send reminder emails.
    def test_no_email_from_page_refreshes(self):
        self.client.force_login(self.user)
        for name in ('dashboard', 'profile', 'notifications'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    # Reject unsafe website addresses in email links.
    def test_site_url_validation(self):
        for value in (
            'javascript:alert(1)',
            'https://user:pass@example.com',
            'https://example.com/?secret=1',
            'https://example.com/path',
            'https://example.com/#fragment'
        ):
            with self.settings(SITE_URL=value), self.assertRaises(CommandError):
                self.run_command()
        with self.settings(DEBUG=False), self.assertRaises(CommandError):
            self.run_command()

    # Use the correct daylight-saving time and support each event type.
    def test_daylight_saving_time_and_event_types(self):
        self.now = datetime(2026, 11, 1, 4, tzinfo=datetime_timezone.utc)
        self.event.event_date = datetime(
            2026,
            11,
            1,
            7,
            tzinfo=datetime_timezone.utc
        )
        self.event.save()
        self.run_command()
        self.assertIn('02:00 AM EST', mail.outbox[0].body)
        for kind in ('follow_up', 'deadline', 'other'):
            self.event.event_type = kind
            self.event.save()
            self.run_command()
        self.assertEqual(len(mail.outbox), 4)


# Test who can change email reminder preferences.
class EmailPreferenceTests(TestCase):

    # Create the sample data needed for each test.
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            'preferences',
            'user@example.com'
        )
        self.other = get_user_model().objects.create_user(
            'other',
            'other@example.com'
        )
        self.client.force_login(self.user)

    # Let users switch reminders on and off only for their own accounts.
    def test_defaults_enable_disable_and_ownership(self):
        self.assertFalse(Profile(user=self.user).email_notifications_enabled)
        other_profile = Profile.objects.create(user=self.other)
        data = {
            'action': 'email_notifications',
            'email_notifications_enabled': 'on',
            'user': self.other.pk
        }
        self.assertRedirects(
            self.client.post(
                reverse('profile'),
                data
            ),
            reverse('profile')
        )
        self.assertTrue(Profile.objects.get(user=self.user).email_notifications_enabled)
        other_profile.refresh_from_db()
        self.assertFalse(other_profile.email_notifications_enabled)
        self.client.logout()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('profile')), 'checked')
        self.client.post(reverse('profile'), {'action': 'email_notifications'})
        self.assertFalse(Profile.objects.get(user=self.user).email_notifications_enabled)

    # Protect preference changes and keep existing profile details.
    def test_missing_email_csrf_auth_and_preserved_profile(self):
        profile = Profile.objects.create(
            user=self.user,
            biography='Keep my bio'
        )
        self.user.email = ''
        self.user.save()
        data = {
            'action': 'email_notifications',
            'email_notifications_enabled': 'on'
        }
        self.assertContains(
            self.client.post(
                reverse('profile'),
                data
            ),
            'Add a valid account email'
        )
        profile.refresh_from_db()
        self.assertFalse(profile.email_notifications_enabled)
        self.assertEqual(profile.biography, 'Keep my bio')
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(
            protected.post(
                reverse('profile'),
                data
            ).status_code,
            403
        )
        self.assertEqual(
            Client().post(
                reverse('profile'),
                data
            ).status_code,
            302
        )


# Test two senders trying to reserve the same reminder at the same time.
class ReminderClaimConcurrencyTests(TransactionTestCase):

    # Allow only one sender to reserve the reminder.
    def test_two_workers_cannot_claim_the_same_record(self):
        now = datetime(2026, 10, 22, 16, tzinfo=datetime_timezone.utc)
        user = get_user_model().objects.create_user('concurrency')
        application = Application.objects.create(
            user=user,
            company='Acme',
            role='Intern'
        )
        event = Event.objects.create(
            application=application,
            event_type='interview',
            event_date=now + timedelta(hours=1)
        )
        reminder = EmailReminder.objects.create(
            user=user,
            event=event,
            schedule_version=1,
            scheduled_for=event.event_date
        )
        barrier = Barrier(2)

        # Try to reserve the reminder from a separate database connection.
        def worker():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                for _ in range(20):
                    try:
                        return claim_reminder(reminder, now)
                    except OperationalError:
                        sleep(0.01)
                raise AssertionError('SQLite remained locked')
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(bool(token) for token in results), 1)
        reminder.refresh_from_db()
        self.assertEqual(reminder.attempts, 1)
