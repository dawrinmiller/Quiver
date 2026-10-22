from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.utils import timezone

from .models import Application, Event, SecurityQuestion, Profile, Document


# Make the job posting link optional on the application form.
class ApplicationForm(forms.ModelForm):
    job_link = forms.URLField(required=False, label='Job link (optional)')

    # Choose the application details people can enter and how the fields appear.
    class Meta:
        model = Application
        fields = [
            'company',
            'role',
            'date_submitted',
            'job_description',
            'job_link',
            'status',
            'notes',
            'resume',
            'cover_letter',
            'rejection_feedback'
        ]
        widgets = {
            'date_submitted': forms.DateInput(attrs={'type': 'date'}),
            'job_description': forms.Textarea(attrs={'rows': 4}),
            'notes': forms.Textarea(attrs={'rows': 3})
        }

    # Only offer this user's documents of the appropriate type.
    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ('resume', 'cover_letter'):
            self.fields[field].queryset = Document.objects.filter(user=user, kind=field) if user else Document.objects.none()
        self.fields['rejection_feedback'].label = 'Did you receive any feedback about why your application was rejected?'
        self.fields['rejection_feedback'].widget = forms.Textarea(attrs={'rows': 4})

    def clean(self):
        data = super().clean()
        if data.get('status') != 'preparing' and not data.get('date_submitted'):
            self.add_error(
                'date_submitted',
                'Enter the actual submission date for a submitted application.'
            )
        return data

    def save(self, commit=True):
        application = super().save(commit=False)
        if 'rejection_feedback' in self.changed_data:
            application.feedback_updated_at = timezone.now()
        if commit:
            application.save()
        return application

    # Prevent a submission date from being set in the future.
    def clean_date_submitted(self):
        value = self.cleaned_data['date_submitted']
        if value and value > timezone.localdate():
            raise forms.ValidationError(
                'Date submitted cannot be in the future. Use an event for a future deadline.'
            )
        return value


# Choose the event details people can enter and provide a date-and-time field.
class EventForm(forms.ModelForm):
    class Meta:
        model = Event
        fields = ['event_type', 'event_date', 'description']
        widgets = {
            'event_date': forms.DateTimeInput(format='%Y-%m-%dT%H:%M', attrs={'type': 'datetime-local'}),
            'description': forms.Textarea(attrs={'rows': 3})
        }

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


# Save optional biography and validate a regular HTTPS LinkedIn profile address.
class ProfileForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['biography', 'linkedin_url']
        labels = {'biography': 'Bio'}
        widgets = {'biography': forms.Textarea(attrs={'rows': 5})}

    def clean_linkedin_url(self):
        from urllib.parse import urlsplit
        value = self.cleaned_data['linkedin_url']
        if value:
            url = urlsplit(value)
            if (
                url.scheme != 'https'
                or url.netloc.lower() not in ('linkedin.com', 'www.linkedin.com')
                or not url.path.startswith('/in/')
                or not url.path[4:].strip('/')
                or url.query
                or url.fragment
            ):
                raise forms.ValidationError(
                    'Use a LinkedIn profile URL such as https://www.linkedin.com/in/your-name/.'
                )
        return value


# Keep reminder preferences separate from biography and account email updates.
class EmailNotificationForm(forms.ModelForm):
    class Meta:
        model = Profile
        fields = ['email_notifications_enabled']
        labels = {'email_notifications_enabled': 'Receive email reminders'}

    # Require a valid account email before enabling reminders.
    def clean_email_notifications_enabled(self):
        from django.core.validators import validate_email
        enabled = self.cleaned_data['email_notifications_enabled']
        if enabled:
            try:
                validate_email(self.instance.user.email)
            except forms.ValidationError:
                raise forms.ValidationError('Add a valid account email address before enabling email reminders.')
        return enabled


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['kind', 'file']

    def clean_file(self):
        from .document_validation import validate_document
        upload = self.cleaned_data['file']
        validate_document(upload)
        return upload
