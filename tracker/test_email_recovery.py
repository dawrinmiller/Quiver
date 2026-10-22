import re
from datetime import timedelta
from smtplib import SMTPException
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from .models import Application, Profile, SecurityQuestion


# Test email recovery without sending real emails.
@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailRecoveryTests(TestCase):

    # Create the sample data needed for each test.
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            'alice',
            'alice@example.com',
            'Original-pass-937!'
        )
        self.security = SecurityQuestion(user=self.user, question='city')
        self.security.set_answer('New York')
        self.security.save()
        self.profile = Profile.objects.create(
            user=self.user,
            biography='My bio'
        )
        self.application = Application.objects.create(
            user=self.user,
            company='Acme',
            role='Engineer'
        )

    # Submit an email address to start password recovery.
    def request_reset(self, email='alice@example.com'):
        return self.client.post(
            reverse('email_password_reset'),
            {'email': email}
        )

    # Build a password-reset link for the sample user.
    def reset_url(self, token=None):
        return reverse(
            'email_password_reset_confirm',
            kwargs={
                'uidb64': urlsafe_base64_encode(force_bytes(self.user.pk)),
                'token': token or default_token_generator.make_token(self.user),
            }
        )

    # Check that both recovery choices open the correct pages.
    def test_selection_and_navigation(self):
        self.assertContains(
            self.client.get(reverse('login')),
            reverse('password_recovery')
        )
        page = self.client.get(reverse('password_recovery'))
        for text in (
            'Forgot Your Password?',
            'Reset with Email',
            'Use Security Question',
            reverse('email_password_reset'),
            reverse('password_reset')
        ):
            self.assertContains(page, text)
        self.assertContains(
            self.client.get(reverse('email_password_reset')),
            'name="email"'
        )
        self.assertContains(
            self.client.get(reverse('password_reset')),
            'name="username"'
        )

    # Check that resetting a password keeps the user's other data unchanged.
    def test_email_reset_and_preserved_data(self):
        original_hash = self.security.answer_hash
        self.assertRedirects(
            self.request_reset(),
            reverse('email_password_reset_done')
        )
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'Reset Your Quiver Password')
        self.assertEqual(message.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(message.to, [self.user.email])
        url = re.search(r'http://testserver(\S+)', message.body).group(1)
        response = self.client.get(url, follow=True)
        self.assertTrue(response.context['validlink'])
        form_url = response.request['PATH_INFO']
        for first, second in [
            (
                '123',
                '123'
            ),
            (
                'New-pass-723!',
                'Different-pass-723!'
            )
        ]:
            response = self.client.post(
                form_url,
                {
                    'new_password1': first,
                    'new_password2': second
                }
            )
            self.assertTrue(response.context['form'].errors)
        self.assertRedirects(
            self.client.post(
                form_url,
                {
                    'new_password1': 'New-pass-723!',
                    'new_password2': 'New-pass-723!',
                    'next': 'https://example.org/'
                }
            ),
            reverse('email_password_reset_complete')
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-pass-723!'))
        self.assertFalse(default_token_generator.check_token(
            self.user,
            url.rstrip('/').split('/')[-1]
        ))
        self.assertFalse(Client().get(url).context['validlink'])
        self.assertTrue(self.client.login(
            username='alice',
            password='New-pass-723!'
        ))
        self.security.refresh_from_db()
        self.profile.refresh_from_db()
        self.application.refresh_from_db()
        self.assertEqual(self.security.answer_hash, original_hash)
        self.assertEqual(self.profile.biography, 'My bio')
        self.assertEqual(self.application.company, 'Acme')
        self.assertEqual(self.user.email, 'alice@example.com')

    # Reject invalid links, expired links, and links for another account.
    def test_invalid_expired_and_wrong_account_tokens(self):
        self.assertFalse(self.client.get(self.reset_url('invalid-token')).context['validlink'])
        now = default_token_generator._now()
        with patch.object(
            default_token_generator,
            '_now',
            return_value=now - timedelta(seconds=3601)
        ):
            expired = default_token_generator.make_token(self.user)
        self.assertFalse(self.client.get(self.reset_url(expired)).context['validlink'])
        other = get_user_model().objects.create_user(
            'bob',
            password='Other-pass-937!'
        )
        self.assertFalse(self.client.get(self.reset_url(default_token_generator.make_token(other))).context['validlink'])

    # Show the same confirmation and limit repeated reset requests.
    def test_generic_confirmation_and_throttling(self):
        known = self.request_reset()
        unknown = self.request_reset('unknown@example.com')
        self.assertEqual(known.url, unknown.url)
        self.assertEqual(len(mail.outbox), 1)
        for _ in range(5):
            self.assertRedirects(
                self.request_reset(),
                reverse('email_password_reset_done')
            )
        self.assertEqual(len(mail.outbox), 3)

    # Keep security-question recovery working when email delivery fails.
    def test_smtp_failure_and_independent_question_recovery(self):
        with patch(
            'tracker.email_recovery.EmailMultiAlternatives.send',
            side_effect=SMTPException('private response')
        ):
            with self.assertLogs(
                'tracker.email_recovery',
                level='WARNING'
            ) as logs:
                self.assertRedirects(
                    self.request_reset(),
                    reverse('email_password_reset_done')
                )
            self.assertNotIn('private response', str(logs.output))
            self.client.post(reverse('password_reset'), {'username': 'alice'})
            self.assertRedirects(
                self.client.post(
                    reverse('password_recovery_question'),
                    {
                'security_answer': 'New York'}
                ),
                reverse('password_reset_confirm')
            )
            self.assertRedirects(
                self.client.post(
                    reverse('password_reset_confirm'),
                    {
                        'new_password1': 'Question-pass-723!',
                        'new_password2': 'Question-pass-723!'
                    }
                ),
                reverse('login')
            )

    # Reject unprotected forms and skip accounts that cannot reset passwords.
    def test_csrf_and_ineligible_accounts(self):
        self.assertEqual(
            Client(enforce_csrf_checks=True).post(
                reverse('email_password_reset'),
                {'email': self.user.email}
            ).status_code,
            403
        )
        self.user.is_active = False
        self.user.save()
        self.request_reset()
        self.user.is_active = True
        self.user.set_unusable_password()
        self.user.save()
        self.request_reset()
        self.assertEqual(len(mail.outbox), 0)
