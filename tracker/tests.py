from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from .models import Application, Event, Notification


# Prepare sample accounts, applications, and events for the tests.
class TrackerTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('alice', 'alice@example.com', 'Original-pass-937!')
        self.other = get_user_model().objects.create_user('bob', 'bob@example.com', 'Original-pass-937!')
        self.application = Application.objects.create(user=self.user, company='Quiver', role='Intern', date_submitted=timezone.localdate())
        self.foreign = Application.objects.create(user=self.other, company='Private Company', role='Engineer', date_submitted=timezone.localdate())
        self.event = Event.objects.create(application=self.application, event_type='interview', event_date=timezone.now() + timedelta(hours=12))
        self.foreign_event = Event.objects.create(application=self.foreign, event_type='other', event_date=timezone.now() + timedelta(days=3))
        self.client.force_login(self.user)

    # Provide sample application details for the tests.
    def data(self, **changes):
        return {'company': 'Example', 'role': 'Developer', 'date_submitted': str(timezone.localdate()), 'status': 'applied', **changes}

    # Check that applications can be created, updated, deleted, and counted correctly.
    def test_application_crud_and_statistics(self):
        response = self.client.post(reverse('new_application'), self.data(user=self.other.pk))
        created = Application.objects.get(company='Example')
        self.assertEqual(created.user, self.user)
        self.assertRedirects(response, reverse('application_detail', args=[created.pk]))
        dashboard = self.client.get(reverse('dashboard'))
        self.assertEqual(dashboard.context['applied_count'], 2)
        self.assertContains(dashboard, 'Example')
        self.assertNotContains(dashboard, 'Private Company')
        for status, key in [('offer', 'offers_count'), ('interviewing', 'interviewing_count'), ('rejected', 'rejected_count')]:
            self.client.post(reverse('application_update', args=[created.pk]), self.data(status=status))
            self.assertEqual(self.client.get(reverse('dashboard')).context[key], 1)
        self.assertEqual(self.client.get(reverse('application_delete', args=[created.pk])).status_code, 405)
        self.client.post(reverse('application_delete', args=[created.pk]))
        self.assertFalse(Application.objects.filter(pk=created.pk).exists())
        self.assertEqual(self.client.get(reverse('dashboard')).context['rejected_count'], 0)

    # Check that missing details and invalid dates are rejected.
    def test_validation(self):
        response = self.client.post(reverse('new_application'), {})
        self.assertTrue(response.context['form'].errors)
        response = self.client.post(reverse('new_application'), self.data(date_submitted=str(timezone.localdate() + timedelta(days=1))))
        self.assertContains(response, 'cannot be in the future')
        response = self.client.post(reverse('new_application'), self.data(company='', status='invalid', job_link='bad'))
        self.assertTrue(response.context['form'].errors)
        response = self.client.post(reverse('event_create', args=[self.application.pk]), {'event_type': 'other', 'event_date': (timezone.now() - timedelta(days=1)).isoformat()})
        self.assertContains(response, 'Choose a future date')

    # Check that events can be managed and the nearest date appears on the dashboard.
    def test_events_and_next_step(self):
        future = timezone.now() + timedelta(hours=2)
        data = {'event_type': 'follow_up', 'event_date': future.isoformat(), 'description': 'Call recruiter', 'application': self.foreign.pk}
        self.client.post(reverse('event_create', args=[self.application.pk]), data)
        event = Event.objects.get(description='Call recruiter')
        self.assertEqual(event.application, self.application)
        row = self.client.get(reverse('dashboard')).context['applications'].get(pk=self.application.pk)
        self.assertEqual(row.next_step_date, future)
        self.assertContains(self.client.get(reverse('application_detail', args=[self.application.pk])), 'Call recruiter')
        self.assertContains(self.client.get(reverse('upcoming_events')), 'Follow-up')
        Notification.objects.create(user=self.user, event=event, title='Test', message='Test')
        self.client.post(reverse('event_update', args=[self.application.pk, event.pk]), {**data, 'event_date': (future + timedelta(hours=1)).isoformat()})
        event.refresh_from_db()
        self.assertFalse(Notification.objects.filter(event=event).exists())
        self.assertEqual(self.client.get(reverse('event_delete', args=[self.application.pk, event.pk])).status_code, 405)
        self.client.post(reverse('event_delete', args=[self.application.pk, event.pk]))
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    # List the private pages that should require someone to sign in.
    def protected_routes(self):
        return [('dashboard', []), ('profile', []), ('new_application', []), ('application_tracker', []),
                ('application_detail', [self.foreign.pk]), ('application_update', [self.foreign.pk]),
                ('application_delete', [self.foreign.pk]), ('event_create', [self.foreign.pk]),
                ('event_update', [self.foreign.pk, self.foreign_event.pk]), ('event_delete', [self.foreign.pk, self.foreign_event.pk]),
                ('upcoming_events', []), ('notifications', []), ('notification_read', [1]), ('notifications_read_all', []), ('password_change', []), ('password_change_done', [])]

    # Check that users cannot access or change someone else's applications or events.
    def test_ownership(self):
        for name, args in self.protected_routes():
            if name.startswith('application_') and args or name.startswith('event_'):
                self.assertEqual(self.client.post(reverse(name, args=args), {}).status_code, 404)
                if 'delete' not in name:
                    self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 404)
        for name in ['event_update', 'event_delete']:
            self.assertEqual(self.client.post(reverse(name, args=[self.application.pk, self.foreign_event.pk]), {}).status_code, 404)
        self.assertTrue(Application.objects.filter(pk=self.foreign.pk).exists())
        self.assertTrue(Event.objects.filter(pk=self.foreign_event.pk).exists())

    # Check that private pages require login and that delete requests need form protection.
    def test_authentication_and_csrf(self):
        self.client.logout()
        for name, args in self.protected_routes():
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 302)
            self.assertEqual(self.client.post(reverse(name, args=args), {}).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('application_delete', args=[self.application.pk])).status_code, 403)

    # Check that changing a password keeps the user signed in and disables the old password.
    def test_password_change(self):
        data = {'old_password': 'wrong', 'new_password1': 'Updated-pass-834!', 'new_password2': 'Updated-pass-834!'}
        self.assertEqual(self.client.post(reverse('password_change'), data).status_code, 200)
        response = self.client.post(reverse('password_change'), {**data, 'old_password': 'Original-pass-937!'})
        self.assertRedirects(response, reverse('password_change_done'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('Original-pass-937!'))
        self.assertTrue(self.client.login(username='alice', password='Updated-pass-834!'))

    # Check account registration, email updates, and signing out.
    def test_registration_profile_logout(self):
        self.client.logout()
        response = self.client.post(reverse('register'), {'username': 'charlie', 'email': 'charlie@example.com', 'password1': 'Register-pass-827!', 'password2': 'Register-pass-827!', 'security_question': 'pet', 'security_answer': 'Sparky'})
        self.assertRedirects(response, reverse('login'))
        self.assertEqual(get_user_model().objects.get(username='charlie').email, 'charlie@example.com')
        self.client.force_login(self.user)
        self.client.post(reverse('profile'), {'email': 'updated@example.com'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'updated@example.com')
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertRedirects(self.client.post(reverse('logout')), reverse('login'))
