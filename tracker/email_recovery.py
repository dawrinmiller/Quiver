import logging

from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordResetForm
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.utils.crypto import salted_hmac
from django.views.generic import TemplateView


logger = logging.getLogger(__name__)


# Send reset emails without exposing private delivery errors.
class RecoveryEmailForm(PasswordResetForm):

    # Build the reset email and safely handle delivery failures.
    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None
    ):
        subject = ''.join(
            render_to_string(
                subject_template_name,
                context
            ).splitlines()
        )
        body = render_to_string(email_template_name, context)
        try:
            EmailMultiAlternatives(
                subject,
                body,
                from_email,
                [to_email]
            ).send()
        except Exception:
            logger.warning('Password recovery email delivery failed.')


# Show the two ways to recover a password.
class RecoverySelectionView(TemplateView):
    template_name = 'tracker/recovery_selection.html'


# Use Django to send a secure password-reset link.
class EmailPasswordResetView(auth_views.PasswordResetView):
    form_class = RecoveryEmailForm
    template_name = 'tracker/email_reset_form.html'
    email_template_name = 'tracker/email_reset_body.txt'
    subject_template_name = 'tracker/email_reset_subject.txt'
    success_url = reverse_lazy('email_password_reset_done')

    # Limit reset requests from each email address and computer for 15 minutes.
    # The default cache keeps a separate count for each server process.
    def form_valid(self, form):
        identities = [
            ('address', form.cleaned_data['email'].casefold(), 3),
            ('ip', self.request.META.get('REMOTE_ADDR', ''), 10),
        ]
        allowed = True

        # Count requests without storing the email address or IP in the cache key.
        for kind, identity, limit in identities:
            digest = salted_hmac(
                'email-password-recovery',
                identity
            ).hexdigest()
            key = f'password-recovery:{kind}:{digest}'
            if cache.add(key, 1, timeout=900):
                count = 1
            else:
                try:
                    count = cache.incr(key)
                except ValueError:
                    cache.add(key, 1, timeout=900)
                    count = 1
            allowed = allowed and count <= limit
        if not allowed:
            # Show the same confirmation even when the request limit is reached.
            return redirect(self.success_url)
        return super().form_valid(form)
