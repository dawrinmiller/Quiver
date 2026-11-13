from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Application, Event


# Check the application details layout and its existing controls.
class ApplicationDetailPresentationTests(TestCase):
    # Create a signed-in owner and an application for each test.
    def setUp(self):
        self.user = get_user_model().objects.create_user('detail-owner')
        self.client.force_login(self.user)
        self.application = Application.objects.create(
            user=self.user,
            company='Example & Company',
            role='Engineer',
            notes='<script>alert(1)</script>\nSecond paragraph',
            job_link='https://example.com/job',
        )
        self.url = reverse('application_detail', args=[self.application.pk])

    # Check section labels, empty messages, and safe display of saved notes.
    def test_sections_fallbacks_and_safe_notes(self):
        response = self.client.get(self.url)
        for text in [
            'Application Overview',
            'Application Notes',
            'Scheduled Events',
            'Application Documents',
            'No documents attached to this application.',
            'No events scheduled yet.',
            'Not submitted',
            'View Original Job Posting',
            'Back to Application Tracker'
        ]:
            self.assertContains(response, text)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, 'rel="noopener noreferrer"')
        # Show the empty notes message when no notes have been saved.
        self.application.notes = ''
        self.application.save()
        self.assertContains(
            self.client.get(self.url),
            'No notes have been added for this application.'
        )

    # Check event order, local dates, and links for each individual event.
    @timezone.override('America/New_York')
    def test_cards_ordering_local_timing_and_event_specific_actions(self):
        # At 00:30 UTC it is still the previous calendar day in New York.
        now = datetime(2026, 11, 15, 0, 30, tzinfo=ZoneInfo('UTC'))
        # Create events before and after the chosen current time.
        events = []
        for hours in [30, 1, -25, -1]:
            events.append(
                Event.objects.create(
                    application=self.application,
                    event_type='interview',
                    event_date=now + timedelta(hours=hours),
                    interviewer_name='Sarah',
                    interviewer_email='sarah@example.com',
                )
            )
        # Render the page using the chosen time instead of the real clock.
        with patch('tracker.views.timezone.now', return_value=now):
            response = self.client.get(self.url)
        self.assertEqual(response.context['event_count'], 4)
        self.assertEqual(
            list(response.context['upcoming_events']),
            [events[1], events[0]]
        )
        self.assertEqual(
            list(response.context['past_events']),
            [events[3], events[2]]
        )
        self.assertContains(response, 'class="application-event-card"', count=4)
        self.assertContains(response, 'Today · Upcoming', count=1)
        self.assertContains(response, 'Today · Past', count=1)
        self.assertContains(response, 'Send Thank-You Email', count=2)
        self.assertNotContains(response, 'Completed')
        # Make sure every edit and delete link uses its own event.
        for event in events:
            self.assertContains(
                response,
                reverse(
                    'event_update',
                    args=[self.application.pk, event.pk]
                )
            )
            self.assertContains(
                response,
                reverse(
                    'event_delete',
                    args=[self.application.pk, event.pk]
                )
            )
        # Future interviews must not offer a thank-you email yet.
        for event in events[:2]:
            self.assertNotContains(
                response,
                reverse('interview_thank_you', args=[event.pk])
            )

    # Prevent another account from viewing this application.
    def test_foreign_application_is_private(self):
        other = get_user_model().objects.create_user('detail-other')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
