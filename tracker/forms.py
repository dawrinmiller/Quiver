from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import Application, Event, SecurityQuestion


class ApplicationForm(forms.ModelForm):
    job_link = forms.URLField(required=False, label='Job link (optional)')

    class Meta:
        model = Application
        fields = ['company', 'role', 'date_submitted', 'job_description', 'job_link', 'status', 'notes']
        widgets = {'date_submitted': forms.DateInput(attrs={'type': 'date'}),
                   'job_description': forms.Textarea(attrs={'rows': 4}),
                   'notes': forms.Textarea(attrs={'rows': 3})}

    def clean_date_submitted(self):
        value = self.cleaned_data['date_submitted']
        if value > timezone.localdate():
            raise forms.ValidationError('Date submitted cannot be in the future. Use an event for a future deadline.')
        return value


class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['event_type', 'event_date', 'description']
        widgets = {'event_date': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
                   'description': forms.Textarea(attrs={'rows': 3})}

    def clean_event_date(self):
        value = self.cleaned_data['event_date']
        # Allow editing notes on a past event without changing its original time.
        if value <= timezone.now() and (not self.instance.pk or value != self.instance.event_date):
            raise forms.ValidationError('Choose a future date and time.')
        return value


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    security_question = forms.ChoiceField(choices=SecurityQuestion.Question.choices)
    security_answer = forms.CharField(max_length=200, widget=forms.PasswordInput, strip=True)

    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ['username', 'email']

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            security = SecurityQuestion(user=user, question=self.cleaned_data['security_question'])
            security.set_answer(self.cleaned_data['security_answer'])
            security.save()
        return user


class ProfileEmailForm(forms.ModelForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = get_user_model()
        fields = ['email']


class SecurityQuestionForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput)
    security_question = forms.ChoiceField(choices=SecurityQuestion.Question.choices)
    security_answer = forms.CharField(max_length=200, widget=forms.PasswordInput, strip=True)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_current_password(self):
        value = self.cleaned_data['current_password']
        if not self.user.check_password(value):
            raise forms.ValidationError('Your current password is incorrect.')
        return value


class RecoveryUsernameForm(forms.Form):
    username = forms.CharField(max_length=150)


class RecoveryAnswerForm(forms.Form):
    security_answer = forms.CharField(max_length=200, widget=forms.PasswordInput, strip=True)
