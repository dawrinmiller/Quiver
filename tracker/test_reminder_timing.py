from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .forms import EventForm
from .models import Application, Event, Notification
from .notifications import create_event_notifications


@override_settings(TIME_ZONE='America/New_York')
class ReminderTimingTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('timing')
        self.application = Application.objects.create(user=self.user, company='Example', role='Intern')
        self.client.force_login(self.user)
        # Noon Eastern, so same-day and next-day boundaries stay deterministic.
        self.now = datetime(2026, 10, 14, 16, tzinfo=datetime_timezone.utc)

    def event(self, when):
        return Event.objects.create(
            application=self.application,
            event_type='interview',
            event_date=when
        )

    def test_same_day_and_next_day_reminders_exclude_past_and_distant_events(self):
        soon = self.event(self.now + timedelta(minutes=30))
        tomorrow = self.event(self.now + timedelta(hours=23))
        self.event(self.now - timedelta(minutes=1))
        self.event(self.now)
        self.event(self.now + timedelta(days=2))
        with patch('tracker.notifications.timezone.now', return_value=self.now):
            for _ in range(2):
                response = self.client.get(reverse('notifications'))
                self.assertEqual(response.context['unread_notification_count'], 2)
        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(Notification.objects.get(event=soon).title, 'Interview Today')
        self.assertIn('12:30 PM', Notification.objects.get(event=soon).message)
        self.assertEqual(
            Notification.objects.get(event=tomorrow).title,
            'Interview Tomorrow'
        )

    def test_overnight_wording_updates_without_reviving_read_reminder(self):
        event = self.event(datetime(2026, 10, 15, 5, tzinfo=datetime_timezone.utc))
        with patch('tracker.notifications.timezone.now', return_value=self.now):
            create_event_notifications(self.user)
        notification = Notification.objects.get(event=event)
        self.assertEqual(notification.title, 'Interview Tomorrow')
        notification.is_read = True
        notification.save()
        # 12:30 a.m. Eastern: the event is now today, in another half hour.
        after_midnight = datetime(2026, 10, 15, 4, 30, tzinfo=datetime_timezone.utc)
        with patch('tracker.notifications.timezone.now', return_value=after_midnight):
            response = self.client.get(reverse('notifications'))
        notification.refresh_from_db()
        self.assertEqual(notification.title, 'Interview Today')
        self.assertTrue(notification.is_read)
        self.assertEqual(response.context['unread_notification_count'], 0)

    def test_application_timestamp_uses_eastern_calendar_date(self):
        # The user's reported 03:12 UTC timestamp is 11:12 p.m. the previous day.
        Application.objects.filter(pk=self.application.pk).update(
            created_at=datetime(2026, 10, 15, 3, 12, tzinfo=datetime_timezone.utc)
        )
        response = self.client.get(reverse('application_detail', args=[self.application.pk]))
        self.assertContains(response, 'Oct 14, 2026, 11:12 p.m. EDT')

    def test_event_input_is_local_and_handles_daylight_saving_time(self):
        with patch('tracker.forms.timezone.now', return_value=self.now):
            for value, expected_offset in [('2027-07-01T14:00', -4), ('2027-01-01T14:00', -5)]:
                form = EventForm({'event_type': 'interview', 'event_date': value})
                self.assertTrue(form.is_valid(), form.errors)
                self.assertEqual(
                    form.cleaned_data['event_date'].utcoffset(),
                    timedelta(hours=expected_offset)
                )

    def test_profile_uses_bio_and_resume_links_only_offer_download(self):
        from django.template.loader import render_to_string
        from .forms import ProfileForm
        self.assertEqual(ProfileForm().fields['biography'].label, 'Bio')
        self.assertContains(self.client.get(reverse('profile')), 'Bio and LinkedIn')
        html = render_to_string(
            'tracker/document_list.html',
            {
                'documents': [
                    {'pk': 1, 'kind': 'resume', 'filename': 'Resume.pdf'},
                ]
            }
        )
        self.assertIn('>Download</a>', html)
        self.assertNotIn('>View</a>', html)
