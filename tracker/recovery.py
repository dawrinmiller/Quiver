from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.hashers import make_password, check_password
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from .forms import RecoveryUsernameForm, RecoveryAnswerForm
from .models import SecurityQuestion


RECOVERY_KEY = 'password_recovery'
MAX_ATTEMPTS = 5
# Unknown accounts still perform a password-hash check and show a question.
DUMMY_ANSWER_HASH = make_password('unused recovery answer')


def recovery_state(request, stage):
    state = request.session.get(RECOVERY_KEY, {})
    if state.get('stage') != stage or state.get('expires_at', 0) <= timezone.now().timestamp():
        request.session.pop(RECOVERY_KEY, None)
        return None
    return state


@never_cache
def password_reset_view(request):
    form = RecoveryUsernameForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        security = SecurityQuestion.objects.filter(user__username=form.cleaned_data['username'], user__is_active=True).first()
        # Keep the previous attempt count when restarting within the same session.
        previous = request.session.get(RECOVERY_KEY, {})
        request.session[RECOVERY_KEY] = {
            'stage': 'answer', 'security_id': security.pk if security else None,
            'question': security.get_question_display() if security else SecurityQuestion.Question.PET.label,
            'expires_at': (timezone.now() + timedelta(minutes=10)).timestamp(),
            'attempts': previous.get('attempts', 0),
            'locked_until': previous.get('locked_until', 0),
        }
        return redirect('password_recovery_question')
    return render(request, 'tracker/password_reset_form.html', {'form': form})


@never_cache
@sensitive_post_parameters('security_answer')
def password_recovery_question_view(request):
    state = recovery_state(request, 'answer')
    if state is None:
        return redirect('password_reset')
    form = RecoveryAnswerForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        now = timezone.now()
        with transaction.atomic():
            security = SecurityQuestion.objects.select_for_update().select_related('user').filter(pk=state['security_id'], user__is_active=True).first()
            session_locked = state.get('locked_until', 0) > now.timestamp()
            account_locked = security and security.locked_until and security.locked_until > now
            if session_locked or account_locked:
                form.add_error(None, 'Recovery is temporarily unavailable. Try again in 15 minutes.')
            else:
                if state.get('locked_until', 0):
                    state['attempts'] = 0
                    state['locked_until'] = 0
                if security and security.locked_until:
                    security.failed_attempts = 0
                    security.locked_until = None
                answer = form.cleaned_data['security_answer']
                valid = security.check_answer(answer) if security else check_password(SecurityQuestion.normalize_answer(answer), DUMMY_ANSWER_HASH)
                if security and valid:
                    security.failed_attempts = 0
                    security.locked_until = None
                    security.save(update_fields=['failed_attempts', 'locked_until'])
                    request.session.cycle_key()
                    request.session[RECOVERY_KEY] = {
                        'stage': 'verified', 'security_id': security.pk,
                        'auth_hash': security.user.get_session_auth_hash(),
                        'answer_hash': security.answer_hash,
                        'expires_at': (now + timedelta(minutes=5)).timestamp(),
                    }
                    return redirect('password_reset_confirm')
                state['attempts'] = state.get('attempts', 0) + 1
                if state['attempts'] >= MAX_ATTEMPTS:
                    state['locked_until'] = (now + timedelta(minutes=15)).timestamp()
                request.session[RECOVERY_KEY] = state
                if security:
                    security.failed_attempts += 1
                    if security.failed_attempts >= MAX_ATTEMPTS:
                        security.locked_until = now + timedelta(minutes=15)
                    security.save(update_fields=['failed_attempts', 'locked_until'])
                form.add_error(None, 'Unable to verify your answer. Check your answer or try again later.')
    return render(request, 'tracker/password_recovery_question.html', {'form': form, 'question': state['question']})


@never_cache
@sensitive_post_parameters('new_password1', 'new_password2')
def password_reset_confirm_view(request):
    state = recovery_state(request, 'verified')
    if state is None:
        return redirect('password_reset')
    security = SecurityQuestion.objects.select_related('user').filter(pk=state['security_id'], user__is_active=True).first()
    if (security is None or not constant_time_compare(state.get('auth_hash', ''), security.user.get_session_auth_hash())
            or not constant_time_compare(state.get('answer_hash', ''), security.answer_hash)):
        request.session.pop(RECOVERY_KEY, None)
        return redirect('password_reset')
    form = SetPasswordForm(security.user, request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        request.session.pop(RECOVERY_KEY, None)
        messages.success(request, 'Your password has been reset successfully. You can now log in.')
        return redirect('login')
    return render(request, 'tracker/password_reset_confirm.html', {'form': form})
