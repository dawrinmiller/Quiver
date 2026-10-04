from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import Application, Event, SecurityQuestion


# Make the job posting link optional on the application form.
class ApplicationForm(forms.ModelForm):
    job_link = forms.URLField(required=False, label='Job link (optional)')

    # Choose the application details people can enter and how the fields appear.
    class Meta:
        model = Application
        fields = ['company', 'role', 'date_submitted', 'job_description', 'job_link', 'status', 'notes']
        widgets = {'date_submitted': forms.DateInput(attrs={'type': 'date'}),
                   'job_description': forms.Textarea(attrs={'rows': 4}),
                   'notes': forms.Textarea(attrs={'rows': 3})}

    # Prevent a submission date from being set in the future.
    def clean_date_submitted(self):
        value = self.cleaned_data['date_submitted']
        if value > timezone.localdate():
            raise forms.ValidationError('Date submitted cannot be in the future. Use an event for a future deadline.')
        return value


# Choose the event details people can enter and provide a date-and-time field.
class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['event_type', 'event_date', 'description']
        widgets = {'event_date': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
                   'description': forms.Textarea(attrs={'rows': 3})}

    # Require a future time for new or rescheduled events.
    def clean_event_date(self):
        value = self.cleaned_data['event_date']
        # Allow editing notes on a past event without changing its original time.
        if value <= timezone.now() and (not self.instance.pk or value != self.instance.event_date):
            raise forms.ValidationError('Choose a future date and time.')
        return value


# Collect an email and security question when someone creates an account.
class RegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    security_question = forms.ChoiceField(choices=SecurityQuestion.Question.choices)
    security_answer = forms.CharField(max_length=200, widget=forms.PasswordInput, strip=True)

    # Save the new account's username and email.
    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ['username', 'email']

    # Create the account and securely store its recovery answer.
    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            security = SecurityQuestion(user=user, question=self.cleaned_data['security_question'])
            security.set_answer(self.cleaned_data['security_answer'])
            security.save()
        return user


# Provide a form for updating the user's email address.
class ProfileEmailForm(forms.ModelForm):
    email = forms.EmailField(required=True)

    # Save the email on the existing account.
    class Meta:
        model = get_user_model()
        fields = ['email']


# Ask for the current password before changing a recovery question.
class SecurityQuestionForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput)
    security_question = forms.ChoiceField(choices=SecurityQuestion.Question.choices)
    security_answer = forms.CharField(max_length=200, widget=forms.PasswordInput, strip=True)

    # Connect the recovery-question form to the signed-in account.
    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    # Check the user's current password before accepting changes.
    def clean_current_password(self):
        value = self.cleaned_data['current_password']
        if not self.user.check_password(value):
            raise forms.ValidationError('Your current password is incorrect.')
        return value


# Ask for a username to begin password recovery.
class RecoveryUsernameForm(forms.Form):
    username = forms.CharField(max_length=150)


# Ask for the security answer without showing it as it is typed.
class RecoveryAnswerForm(forms.Form):
    security_answer = forms.CharField(max_length=200, widget=forms.PasswordInput, strip=True)
