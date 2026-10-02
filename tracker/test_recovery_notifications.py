from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Application, Event, Notification, SecurityQuestion
from .notifications import create_event_notifications
from .recovery import RECOVERY_KEY


class RecoveryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('alice', 'alice@example.com', 'Original-pass-937!')
        self.security = SecurityQuestion(user=self.user, question='city')
        self.security.set_answer('New York')
        self.security.save()

    def start_recovery(self, client=None, username='alice'):
        client = client or self.client
        return client.post(reverse('password_reset'), {'username': username})

    def verify_answer(self, client=None):
        client = client or self.client
        self.start_recovery(client)
        return client.post(reverse('password_recovery_question'), {'security_answer': '  NEW   york  '})

    def test_registration_requires_and_hashes_security_answer(self):
        data = {'username': 'newuser', 'email': 'new@example.com', 'password1': 'Register-pass-827!', 'password2': 'Register-pass-827!',
                'security_question': 'pet', 'security_answer': '  Sparky  '}
        self.assertRedirects(self.client.post(reverse('register'), data), reverse('login'))
        security = SecurityQuestion.objects.get(user__username='newuser')
        self.assertNotEqual(security.answer_hash, 'Sparky')
        self.assertNotIn('Sparky', security.answer_hash)
        self.assertTrue(security.check_answer('sparky'))
        data['username'] = 'missingquestion'
        data.pop('security_question')
        self.assertEqual(self.client.post(reverse('register'), data).status_code, 200)
        self.assertFalse(get_user_model().objects.filter(username='missingquestion').exists())

    def test_recovery_flow_and_single_use_authorization(self):
        self.assertContains(self.client.get(reverse('login')), reverse('password_reset'))
        self.assertRedirects(self.start_recovery(), reverse('password_recovery_question'))
        self.assertContains(self.client.get(reverse('password_recovery_question')), 'What city were you born in?')
        self.assertContains(self.client.post(reverse('password_recovery_question'), {'security_answer': 'wrong'}), 'Unable to verify')
        self.assertRedirects(self.client.get(reverse('password_reset_confirm')), reverse('password_reset'))
        self.assertRedirects(self.verify_answer(), reverse('password_reset_confirm'))
        response = self.client.post(reverse('password_reset_confirm'), {'new_password1': '123', 'new_password2': '123'})
        self.assertTrue(response.context['form'].errors)
        response = self.client.post(reverse('password_reset_confirm'), {'new_password1': 'Reset-pass-723!', 'new_password2': 'Different-pass-723!'})
        self.assertTrue(response.context['form'].errors)
        response = self.client.post(reverse('password_reset_confirm'), {'new_password1': 'Reset-pass-723!', 'new_password2': 'Reset-pass-723!'}, follow=True)
        self.assertRedirects(response, reverse('login'))
        self.assertContains(response, 'Your password has been reset successfully.')
        self.assertNotIn(RECOVERY_KEY, self.client.session)
        self.assertFalse(self.client.login(username='alice', password='Original-pass-937!'))
        self.assertTrue(self.client.login(username='alice', password='Reset-pass-723!'))
        self.assertRedirects(self.client.get(reverse('password_reset_confirm')), reverse('password_reset'))

    def test_cannot_bypass_or_use_another_session(self):
        self.assertRedirects(self.client.get(reverse('password_recovery_question')), reverse('password_reset'))
        self.assertRedirects(self.client.get(reverse('password_reset_confirm')), reverse('password_reset'))
        self.assertRedirects(self.client.post(reverse('password_reset_confirm'), {'new_password1': 'Bypass-pass-937!', 'new_password2': 'Bypass-pass-937!'}), reverse('password_reset'))
        self.verify_answer()
        self.assertRedirects(Client().get(reverse('password_reset_confirm')), reverse('password_reset'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Original-pass-937!'))

    def test_unknown_and_unconfigured_accounts_fail_gracefully(self):
        get_user_model().objects.create_user('legacy', password='Original-pass-937!')
        for username in ['unknown', 'legacy']:
            self.assertRedirects(self.start_recovery(username=username), reverse('password_recovery_question'))
            self.assertContains(self.client.get(reverse('password_recovery_question')), 'What was the name of your first pet?')
            self.assertContains(self.client.post(reverse('password_recovery_question'), {'security_answer': 'unused recovery answer'}), 'Unable to verify')
            self.assertNotEqual(self.client.session[RECOVERY_KEY]['stage'], 'verified')

    def test_attempt_limit_survives_new_session(self):
        self.start_recovery()
        for _ in range(5):
            self.client.post(reverse('password_recovery_question'), {'security_answer': 'wrong'})
        other_client = Client()
        self.start_recovery(other_client)
        response = other_client.post(reverse('password_recovery_question'), {'security_answer': 'New York'})
        self.assertContains(response, 'temporarily unavailable')
        self.assertEqual(other_client.session[RECOVERY_KEY]['stage'], 'answer')
        self.security.refresh_from_db()
        self.assertIsNotNone(self.security.locked_until)
        self.security.locked_until = timezone.now() - timedelta(minutes=1)
        self.security.save()
        self.assertRedirects(other_client.post(reverse('password_recovery_question'), {'security_answer': 'New York'}), reverse('password_reset_confirm'))

    def test_expiration_password_and_security_changes_revoke_grant(self):
        self.verify_answer()
        session = self.client.session
        state = session[RECOVERY_KEY]
        state['expires_at'] = (timezone.now() - timedelta(seconds=1)).timestamp()
        session[RECOVERY_KEY] = state
        session.save()
        self.assertRedirects(self.client.get(reverse('password_reset_confirm')), reverse('password_reset'))
        self.verify_answer()
        self.user.set_password('Changed-pass-937!')
        self.user.save()
        self.assertRedirects(self.client.get(reverse('password_reset_confirm')), reverse('password_reset'))
        self.verify_answer()
        self.security.set_answer('Boston')
        self.security.save()
        self.assertRedirects(self.client.get(reverse('password_reset_confirm')), reverse('password_reset'))

    def test_existing_user_profile_setup_requires_current_password(self):
        self.security.delete()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('profile')), 'Set up a security question')
        data = {'action': 'security', 'current_password': 'wrong', 'security_question': 'school', 'security_answer': 'Oak School'}
        self.assertContains(self.client.post(reverse('profile'), data), 'current password is incorrect')
        self.assertFalse(SecurityQuestion.objects.filter(user=self.user).exists())
        self.assertRedirects(self.client.post(reverse('profile'), {**data, 'current_password': 'Original-pass-937!'}), reverse('profile'))
        security = SecurityQuestion.objects.get(user=self.user)
        self.assertTrue(security.check_answer('oak school'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'alice@example.com')


@override_settings(TIME_ZONE='America/New_York')
class NotificationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('alice', password='Original-pass-937!')
        self.other = get_user_model().objects.create_user('bob', password='Original-pass-937!')
        self.application = Application.objects.create(user=self.user, company='Example', role='Intern', date_submitted=timezone.localdate())
        self.other_application = Application.objects.create(user=self.other, company='Private Company', role='Engineer', date_submitted=timezone.localdate())
        self.tomorrow = timezone.make_aware(datetime.combine(timezone.localdate() + timedelta(days=1), time(14)))
        self.event = Event.objects.create(application=self.application, event_type='interview', event_date=self.tomorrow)
        self.other_event = Event.objects.create(application=self.other_application, event_type='interview', event_date=self.tomorrow)
        self.client.force_login(self.user)

    def test_tomorrow_only_no_duplicates_and_unread_count(self):
        Event.objects.create(application=self.application, event_type='other', event_date=self.tomorrow + timedelta(days=1))
        Event.objects.create(application=self.application, event_type='other', event_date=self.tomorrow - timedelta(days=1))
        for _ in range(2):
            response = self.client.get(reverse('dashboard'))
            self.assertEqual(response.context['unread_notification_count'], 1)
        self.assertEqual(Notification.objects.count(), 1)
        notification = Notification.objects.get()
        self.assertEqual(notification.user, self.user)
        self.assertEqual(notification.event, self.event)
        self.assertIn('2:00 PM', notification.message)
        self.assertContains(self.client.get(reverse('notifications')), 'Interview Tomorrow')
        self.assertNotContains(self.client.get(reverse('notifications')), 'Private Company')

    def test_calendar_boundary_uses_local_timezone(self):
        self.event.delete()
        start = self.tomorrow.replace(hour=0)
        Event.objects.create(application=self.application, event_type='other', event_date=start)
        Event.objects.create(application=self.application, event_type='other', event_date=start + timedelta(days=1))
        create_event_notifications(self.user)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    def test_mark_read_and_all_read_are_scoped_post_only(self):
        create_event_notifications(self.user)
        create_event_notifications(self.other)
        own = Notification.objects.get(user=self.user)
        foreign = Notification.objects.get(user=self.other)
        self.assertEqual(self.client.get(reverse('notification_read', args=[own.pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse('notification_read', args=[foreign.pk])).status_code, 404)
        self.assertRedirects(self.client.post(reverse('notification_read', args=[own.pk])), reverse('notifications'))
        self.assertEqual(self.client.get(reverse('dashboard')).context['unread_notification_count'], 0)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)
        own.is_read = False
        own.save()
        self.assertRedirects(self.client.post(reverse('notifications_read_all')), reverse('notifications'))
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_read)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('notification_read', args=[own.pk])).status_code, 403)

    def test_edit_and_delete_events_and_application_cleanup(self):
        create_event_notifications(self.user)
        data = {'event_type': 'follow_up', 'event_date': (self.tomorrow + timedelta(days=2)).isoformat(), 'description': 'Updated'}
        self.client.post(reverse('event_update', args=[self.application.pk, self.event.pk]), data)
        self.assertFalse(Notification.objects.filter(event=self.event).exists())
        self.client.get(reverse('dashboard'))
        self.assertFalse(Notification.objects.filter(event=self.event).exists())
        self.client.post(reverse('event_update', args=[self.application.pk, self.event.pk]), {**data, 'event_date': self.tomorrow.isoformat()})
        self.client.get(reverse('dashboard'))
        self.assertEqual(Notification.objects.get(event=self.event).title, 'Follow-up Tomorrow')
        self.client.post(reverse('event_delete', args=[self.application.pk, self.event.pk]))
        self.assertFalse(Notification.objects.filter(user=self.user).exists())
        event = Event.objects.create(application=self.application, event_type='other', event_date=self.tomorrow)
        create_event_notifications(self.user)
        self.client.post(reverse('application_delete', args=[self.application.pk]))
        self.assertFalse(Notification.objects.filter(event_id=event.pk).exists())
