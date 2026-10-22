from django.conf import settings
from django.db import models
from django.db import transaction
from django.contrib.auth.hashers import make_password, check_password


# Define an application record and the possible progress stages.
class Application(models.Model):
    class Status(models.TextChoices):
        PREPARING = 'preparing', 'Preparing'
        ONLINE_INTERVIEW = 'online_interview', 'Online Interview'
        ONSITE_INTERVIEW = 'onsite_interview', 'On-site Interview'
        APPLIED = 'applied', 'Applied'
        INTERVIEWING = 'interviewing', 'Interviewing'
        OFFER = 'offer', 'Offer'
        REJECTED = 'rejected', 'Rejected'

    # Store each application's owner, job details, notes, and saved dates.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='applications'
    )
    company = models.CharField(max_length=200)
    role = models.CharField('job title', max_length=200)
    date_submitted = models.DateField(null=True, blank=True)
    job_description = models.TextField(blank=True)
    job_link = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    notes = models.TextField(blank=True)
    resume = models.ForeignKey(
        'Document',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='resume_applications'
    )
    cover_letter = models.ForeignKey(
        'Document',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='cover_letter_applications'
    )
    rejection_feedback = models.TextField(blank=True)
    feedback_updated_at = models.DateTimeField(null=True, blank=True)
    reached_interview = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        # Remember interviews when the application later receives an outcome.
        if self.status in ('interviewing', 'online_interview', 'onsite_interview'):
            self.reached_interview = True
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'reached_interview'}
        super().save(*args, **kwargs)

    # Show the newest submitted applications first.
    class Meta:
        ordering = ['-date_submitted', '-pk']

    # Give each application a readable company-and-job name.
    def __str__(self):
        return f'{self.company} - {self.role}'


# Define an event record and the available kinds of important dates.
class Event(models.Model):
    class Type(models.TextChoices):
        INTERVIEW = 'interview', 'Interview'
        FOLLOW_UP = 'follow_up', 'Follow-up'
        DEADLINE = 'deadline', 'Application deadline'
        OTHER = 'other', 'Other'

    # Store the event's application, time, and optional description.
    application = models.ForeignKey(
        Application,
        on_delete=models.CASCADE,
        related_name='events'
    )
    event_type = models.CharField(max_length=20, choices=Type.choices)
    event_date = models.DateTimeField('event date/time')
    description = models.TextField(blank=True)
    schedule_version = models.PositiveIntegerField(default=1, editable=False)

    # Allow a new reminder when the event time or type changes.
    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk:
                previous = Event.objects.get(pk=self.pk)
                fields = kwargs.get('update_fields')
                changed = any(
                    getattr(previous, field) != getattr(self, field)
                    for field in ('event_date', 'event_type')
                    if fields is None or field in fields
                )
                self.schedule_version = previous.schedule_version + int(changed)
                if changed and fields is not None:
                    kwargs['update_fields'] = set(fields) | {'schedule_version'}
            super().save(*args, **kwargs)

    # Show events in order of when they happen.
    class Meta:
        ordering = ['event_date', 'pk']

    # Give each event a readable name.
    def __str__(self):
        return f'{self.get_event_type_display()} - {self.application}'


# Store each user's event reminders and whether they have been read.
class Notification(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications'
    )
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='notification')
    title = models.CharField(max_length=100)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    # Show the newest notifications first.
    class Meta:
        ordering = ['-created_at', '-pk']


# Define the recovery questions users can choose from.
class SecurityQuestion(models.Model):
    class Question(models.TextChoices):
        PET = 'pet', 'What was the name of your first pet?'
        CITY = 'city', 'What city were you born in?'
        SCHOOL = 'school', 'What was the name of your elementary school?'
        FRIEND = 'friend', 'What was the name of your childhood best friend?'
        CAR = 'car', 'What was the make/model of your first car?'

    # Store the account's question, protected answer, and temporary attempt limits.
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='security_question'
    )
    question = models.CharField(max_length=20, choices=Question.choices)
    answer_hash = models.CharField(max_length=128)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    # Ignore extra spaces and capitalization when comparing recovery answers.
    @staticmethod
    def normalize_answer(answer):
        return ' '.join(answer.split()).casefold()

    # Protect the recovery answer before saving it.
    def set_answer(self, answer):
        self.answer_hash = make_password(self.normalize_answer(answer))

    # Check whether an answer matches the protected answer on file.
    def check_answer(self, answer):
        return check_password(self.normalize_answer(answer), self.answer_hash)


# Store optional profile information without changing Django's account model.
class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    biography = models.TextField(blank=True)
    linkedin_url = models.URLField(blank=True)
    email_notifications_enabled = models.BooleanField(default=False)


# Email delivery never changes the in-app notification or its read status.
class EmailReminder(models.Model):
    # List the possible delivery states for a reminder.
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENDING = 'sending', 'Sending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='email_reminders'
    )
    schedule_version = models.PositiveIntegerField()
    scheduled_for = models.DateTimeField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING
    )
    attempts = models.PositiveSmallIntegerField(default=0)
    claim_token = models.CharField(max_length=32, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=80, blank=True)

    # Keep only one delivery record for each event schedule.
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=[
                    'event',
                    'schedule_version'
                ],
                name='unique_email_event_schedule'
            )
        ]


def document_path(instance, filename):
    # Random storage names keep the original filename out of file paths.
    from pathlib import Path
    from uuid import uuid4
    return f'documents/{instance.user_id}/{uuid4().hex}{Path(filename).suffix.lower()}'


class Document(models.Model):
    class Kind(models.TextChoices):
        RESUME = 'resume', 'Resume'
        COVER_LETTER = 'cover_letter', 'Cover Letter'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='documents'
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    file = models.FileField(upload_to=document_path)
    filename = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at', '-pk']

    def __str__(self):
        return self.filename
