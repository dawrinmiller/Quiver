from datetime import date, datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .health import calculate_health
from .models import Application, Document, EmailReminder, Event, Notification
from .planning import calendar_url


# Test the new pages against the existing application and event records.
@override_settings(TIME_ZONE='America/New_York')
class PlanningTests(TestCase):
    # Create sample accounts and applications for the tests.
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('planner')
        cls.other = get_user_model().objects.create_user('private-planner')
        cls.application = Application.objects.create(
            user=cls.user,
            company='Example Company',
            role='Developer',
            status='applied',
            date_submitted=date(2026, 1, 15),
            notes='Keep these notes',
            rejection_feedback='Existing feedback'
        )
        cls.foreign = Application.objects.create(
            user=cls.other,
            company='Private Company',
            role='Secret role',
            status='preparing'
        )
        cls.preparing = Application.objects.create(
            user=cls.user,
            company='Draft Company',
            role='Intern',
            status='preparing'
        )

    # Sign in for each test without changing the shared application records.
    def setUp(self):
        self.client.force_login(self.user)
        self.board = reverse('application_board')
        self.calendar = reverse('job_search_calendar')
        self.status_url = reverse('application_status', args=[self.application.pk])

    # Create an event at an explicitly local time for timezone tests.
    def event(self, day=15, hour=14, month=6, year=2028, application=None, **fields):
        return Event.objects.create(
            application=application or self.application,
            event_type=fields.pop('event_type', 'interview'),
            event_date=datetime(
                year,
                month,
                day,
                hour,
                tzinfo=ZoneInfo('America/New_York')
            ),
            **fields
        )

    # Require sign-in for every new page and the status update action.
    def test_authentication(self):
        anonymous = Client()
        for url in (self.board, self.calendar, reverse('calendar_event_create')):
            with self.subTest(url=url):
                self.assertEqual(anonymous.get(url).status_code, 302)
        self.assertEqual(
            anonymous.post(self.status_url, {'status': 'offer'}).status_code,
            302
        )

    # Show every real status while keeping another user's applications private.
    def test_board_columns_counts_and_ownership(self):
        self.event()
        response = self.client.get(self.board)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Private Company')
        columns = response.context['columns']
        self.assertEqual(
            [(column['value'], column['label']) for column in columns],
            list(Application.Status.choices)
        )
        counts = {
            column['value']: len(column['applications'])
            for column in columns
        }
        self.assertEqual(counts['applied'], 1)
        self.assertEqual(counts['preparing'], 1)
        self.assertContains(response, 'Next event:')
        self.assertContains(response, 'No applications in this stage.')
        self.assertContains(response, 'Update Status')

    # Keep a helpful message when the user has not added applications.
    def test_board_empty(self):
        self.client.force_login(self.other)
        self.foreign.delete()
        self.assertContains(
            self.client.get(self.board),
            "You haven't added any applications yet.",
            html=False
        )

    # Use every supported status without changing an existing submission date.
    def test_all_statuses_preserve_dates_and_interview_history(self):
        for status, label in Application.Status.choices:
            with self.subTest(status=status):
                response = self.client.post(self.status_url, {'status': status})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['label'], label)
                self.application.refresh_from_db()
                self.assertEqual(self.application.status, status)
                self.assertEqual(self.application.date_submitted, date(2026, 1, 15))
        self.assertTrue(self.application.reached_interview)
        self.assertEqual(Event.objects.count(), 0)

    # Validate missing, future, and malformed submission dates before saving.
    def test_preparing_requires_a_real_date_before_submission(self):
        url = reverse('application_status', args=[self.preparing.pk])
        for values in (
            {'status': 'applied'},
            {
                'status': 'applied',
                'date_submitted': 'bad-date'
            },
            {
                'status': 'applied',
                'date_submitted': str(timezone.localdate() + timedelta(days=1))
            }
        ):
            with self.subTest(values=values):
                response = self.client.post(url, values)
                self.assertEqual(response.status_code, 400)
                self.assertIn('date_submitted', response.json()['errors'])
                self.preparing.refresh_from_db()
                self.assertEqual(self.preparing.status, 'preparing')
                self.assertIsNone(self.preparing.date_submitted)
        response = self.client.post(
            url,
            {
                'status': 'applied',
                'date_submitted': '2026-01-20'
            }
        )
        self.assertEqual(response.status_code, 200)
        self.preparing.refresh_from_db()
        self.assertEqual(self.preparing.date_submitted, date(2026, 1, 20))

    # Allow an explicit date edit and preserve blank dates for Preparing jobs.
    def test_explicit_dates_and_preparing(self):
        self.assertEqual(self.client.post(self.status_url, {
            'status': 'offer',
            'date_submitted': '2026-01-16'
        }).status_code, 200)
        self.application.refresh_from_db()
        self.assertEqual(self.application.date_submitted, date(2026, 1, 16))
        url = reverse('application_status', args=[self.preparing.pk])
        self.assertEqual(
            self.client.post(url, {'status': 'preparing'}).status_code,
            200
        )
        self.preparing.refresh_from_db()
        self.assertIsNone(self.preparing.date_submitted)

    # Reject invalid statuses, missing records, and attempts to change private records.
    def test_status_rejections(self):
        self.assertEqual(self.client.get(self.status_url).status_code, 405)
        self.assertEqual(
            self.client.post(self.status_url, {'status': 'invented'}).status_code,
            400
        )
        for pk in (self.foreign.pk, 999999):
            url = reverse('application_status', args=[pk])
            self.assertEqual(
                self.client.post(url, {'status': 'offer'}).status_code,
                404
            )
        self.application.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertEqual(self.application.status, 'applied')
        self.assertEqual(self.foreign.status, 'preparing')

    # Require the normal Django CSRF token on status changes.
    def test_status_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(self.status_url, {'status': 'offer'}).status_code,
            403
        )
        client.get(self.board)
        token = client.cookies['csrftoken'].value
        response = client.post(
            self.status_url,
            {'status': 'offer'},
            HTTP_X_CSRFTOKEN=token
        )
        self.assertEqual(response.status_code, 200)

    # Ignore unrelated posted fields while preserving notes, feedback, and documents.
    def test_status_updates_only_allowed_fields(self):
        document = Document.objects.create(
            user=self.user,
            kind='resume',
            filename='resume.pdf',
            file='documents/sample.pdf'
        )
        self.application.resume = document
        self.application.save()
        old_updated = self.application.updated_at
        self.client.post(self.status_url, {
            'status': 'online_interview',
            'company': 'Overwrite',
            'user': self.other.pk,
            'notes': 'Overwrite',
            'resume': ''
        })
        self.application.refresh_from_db()
        self.assertEqual(self.application.company, 'Example Company')
        self.assertEqual(self.application.notes, 'Keep these notes')
        self.assertEqual(self.application.rejection_feedback, 'Existing feedback')
        self.assertEqual(self.application.resume_id, document.pk)
        self.assertEqual(self.application.user_id, self.user.pk)
        self.assertGreater(self.application.updated_at, old_updated)

    # Read the updated record through the existing dashboard, edit page, and score.
    def test_status_changes_synchronize_existing_pages(self):
        self.client.post(self.status_url, {'status': 'offer'})
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.context['offers_count'], 1)
        self.assertEqual(dashboard.context['preparing_count'], 1)
        detail = self.client.get(
            reverse('application_detail', args=[self.application.pk])
        )
        self.assertContains(detail, 'Offer')
        edit = self.client.get(
            reverse('application_update', args=[self.application.pk])
        )
        self.assertEqual(edit.context['form']['status'].value(), 'offer')
        health = calculate_health(self.user.applications.all())
        self.assertEqual(health['submitted'], 1)
        self.assertEqual(health['offers'], 1)

    # Show an empty selected day as well as an empty month.
    def test_empty_calendar(self):
        response = self.client.get(self.calendar, {
            'year': 2028,
            'month': 6
        })
        self.assertContains(response, 'No events scheduled for this month.')
        self.assertContains(response, 'No events scheduled for this day.')

    # Display only owned events and their existing application links.
    def test_calendar_events_details_and_ownership(self):
        event = self.event(description='<script>private text</script>')
        self.event(application=self.foreign, description='Foreign secret')
        response = self.client.get(self.calendar, {
            'year': 2028,
            'month': 6,
            'day': 15
        })
        self.assertEqual(response.context['selected_events'], [event])
        self.assertNotContains(response, 'Foreign secret')
        self.assertNotContains(response, 'Private Company')
        self.assertContains(
            response,
            reverse('application_detail', args=[self.application.pk])
        )
        self.assertContains(response, '2:00 p.m. EDT')
        self.assertContains(response, '&lt;script&gt;private text&lt;/script&gt;')

    # Keep all events available even when the month cell shows only two previews.
    def test_multiple_events_and_more_link(self):
        for hour in (9, 10, 11, 12):
            self.event(hour=hour)
        response = self.client.get(self.calendar, {
            'year': 2028,
            'month': 6,
            'day': 15
        })
        cell = next(
            cell
            for week in response.context['weeks']
            for cell in week
            if cell and cell['date'].day == 15
        )
        self.assertEqual(cell['count'], 4)
        self.assertEqual(len(cell['events']), 2)
        self.assertEqual(cell['more'], 2)
        self.assertEqual(len(response.context['selected_events']), 4)
        self.assertContains(response, '+2 more')

    # Build correct month grids, including leap years and Sunday-first weekdays.
    def test_leap_year_and_weekdays(self):
        for year, month, expected_days in ((2028, 2, 29), (2027, 2, 28), (2026, 11, 30)):
            response = self.client.get(self.calendar, {
                'year': year,
                'month': month
            })
            cells = [
                cell
                for week in response.context['weeks']
                for cell in week
                if cell
            ]
            self.assertEqual(len(cells), expected_days)
            self.assertEqual(cells[-1]['date'].day, expected_days)
        november = self.client.get(self.calendar, {
            'year': 2026,
            'month': 11
        })
        self.assertEqual(november.context['weeks'][0][0]['date'], date(2026, 11, 1))

    # Navigate across year boundaries without accepting unbounded dates.
    def test_navigation_and_invalid_dates(self):
        response = self.client.get(self.calendar, {
            'year': 2028,
            'month': 12
        })
        self.assertEqual(response.context['next_url'], calendar_url(date(2029, 1, 1)))
        january = self.client.get(self.calendar, {
            'year': 2029,
            'month': 1
        })
        self.assertEqual(
            january.context['previous_url'],
            calendar_url(date(2028, 12, 1))
        )
        for query in (
            {'year': 'bad'},
            {'month': 13},
            {'year': 9999},
            {
                'year': 2027,
                'month': 2,
                'day': 29
            }
        ):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(self.calendar, query).status_code, 400)

    # Default to the local date rather than the UTC date around midnight.
    @patch('django.utils.timezone.now')
    def test_current_month_uses_local_timezone(self, now):
        now.return_value = datetime(2028, 7, 1, 2, tzinfo=datetime_timezone.utc)
        self.client.force_login(self.user)
        response = self.client.get(self.calendar)
        self.assertEqual(response.context['month'], date(2028, 6, 1))
        self.assertEqual(response.context['selected_day'], date(2028, 6, 30))

    # Include local month boundaries and both repeated hours when daylight saving ends.
    def test_timezone_boundaries_and_daylight_saving(self):
        before = self.event(day=31, month=5, hour=23)
        first = self.event(day=1, month=6, hour=0)
        last = self.event(day=30, month=6, hour=23)
        after = self.event(day=1, month=7, hour=0)
        response = self.client.get(self.calendar, {
            'year': 2028,
            'month': 6,
            'day': 1
        })
        events = [
            event
            for week in response.context['weeks']
            for cell in week
            if cell
            for event in cell['events']
        ]
        self.assertIn(first, events)
        self.assertIn(last, events)
        self.assertNotIn(before, events)
        self.assertNotIn(after, events)
        for fold in (0, 1):
            Event.objects.create(
                application=self.application,
                event_type='other',
                event_date=datetime(
                    2028,
                    11,
                    5,
                    1,
                    30,
                    tzinfo=ZoneInfo('America/New_York'),
                    fold=fold
                )
            )
        november = self.client.get(self.calendar, {
            'year': 2028,
            'month': 11,
            'day': 5
        })
        self.assertEqual(len(november.context['selected_events']), 2)

    # Select an owned application and open the original event form with a date filled in.
    def test_calendar_creation_choice_and_prefill(self):
        url = reverse('calendar_event_create')
        response = self.client.get(url, {'date': '2028-06-15'})
        self.assertNotIn(
            self.foreign,
            response.context['form'].fields['application'].queryset
        )
        denied = self.client.get(
            url,
            {
                'application': self.foreign.pk,
                'date': '2028-06-15'
            }
        )
        self.assertTrue(denied.context['form'].errors)
        response = self.client.get(
            url,
            {
                'application': self.application.pk,
                'date': '2028-06-15'
            }
        )
        self.assertEqual(response.status_code, 302)
        form_page = self.client.get(response.url)
        self.assertEqual(
            timezone.localtime(form_page.context['form'].initial['event_date']).date(),
            date(2028, 6, 15)
        )
        self.assertContains(form_page, 'from_calendar')

    # Create, reschedule, and delete using the existing event views, then return to the calendar.
    def test_calendar_event_lifecycle_and_reminder_rules(self):
        future = timezone.localdate() + timedelta(days=30)
        create_url = reverse('event_create', args=[self.application.pk])
        data = {
            'event_type': 'interview',
            'event_date': future.isoformat() + 'T14:00',
            'description': 'Original meeting',
            'from_calendar': '1'
        }
        response = self.client.post(create_url, data)
        event = Event.objects.get(application=self.application)
        self.assertEqual(response.url, calendar_url(event.event_date))
        self.assertContains(self.client.get(response.url), 'Original meeting')
        Notification.objects.create(
            user=self.user,
            event=event,
            title='Reminder',
            message='Soon',
            is_read=True
        )
        EmailReminder.objects.create(
            user=self.user,
            event=event,
            schedule_version=1,
            scheduled_for=event.event_date,
            status='sent'
        )
        updated_day = future + timedelta(days=1)
        update_url = reverse('event_update', args=[self.application.pk, event.pk])
        response = self.client.post(update_url, {
            **data,
            'event_date': updated_day.isoformat() + 'T15:00',
            'description': 'Rescheduled meeting'
        })
        event.refresh_from_db()
        self.assertEqual(event.schedule_version, 2)
        self.assertFalse(Notification.objects.filter(event=event).exists())
        self.assertEqual(event.email_reminders.get().schedule_version, 1)
        self.assertContains(self.client.get(response.url), 'Rescheduled meeting')
        delete_url = reverse('event_delete', args=[self.application.pk, event.pk])
        response = self.client.post(delete_url, {'from_calendar': '1'})
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())
        self.assertContains(
            self.client.get(response.url),
            'No events scheduled for this day.'
        )

    # Keep existing notifications read when the calendar is simply viewed.
    def test_calendar_does_not_reset_read_notifications(self):
        event = Event.objects.create(
            application=self.application,
            event_type='follow_up',
            event_date=timezone.now() + timedelta(hours=1)
        )
        notification = Notification.objects.create(
            user=self.user,
            event=event,
            title='Reminder',
            message='Soon',
            is_read=True
        )
        self.client.get(calendar_url(event.event_date))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertEqual(Notification.objects.filter(event=event).count(), 1)

    # Keep the original event ownership rules on direct edit and delete requests.
    def test_private_event_actions_denied(self):
        event = self.event(application=self.foreign)
        edit = reverse('event_update', args=[self.foreign.pk, event.pk])
        delete = reverse('event_delete', args=[self.foreign.pk, event.pk])
        self.assertEqual(self.client.get(edit + '?from_calendar=1').status_code, 404)
        self.assertEqual(
            self.client.post(delete, {'from_calendar': '1'}).status_code,
            404
        )

    # Avoid one event query for every card or day cell.
    def test_page_queries_remain_bounded(self):
        with patch('tracker.notifications.create_event_notifications'):
            with self.assertNumQueries(4):
                self.client.get(self.board)
            with self.assertNumQueries(4):
                self.client.get(self.calendar)
