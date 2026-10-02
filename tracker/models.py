from django.conf import settings
from django.db import models
from django.contrib.auth.hashers import make_password, check_password


class Application(models.Model):
    class Status(models.TextChoices):
        APPLIED = 'applied', 'Applied'
        INTERVIEWING = 'interviewing', 'Interviewing'
        OFFER = 'offer', 'Offer'
        REJECTED = 'rejected', 'Rejected'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='applications')
    company = models.CharField(max_length=200)
    role = models.CharField('job title', max_length=200)
    date_submitted = models.DateField()
    job_description = models.TextField(blank=True)
    job_link = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.APPLIED)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date_submitted', '-pk']

    def __str__(self):
        return f'{self.company} - {self.role}'


class Event(models.Model):
    class Type(models.TextChoices):
        INTERVIEW = 'interview', 'Interview'
        FOLLOW_UP = 'follow_up', 'Follow-up'
        DEADLINE = 'deadline', 'Application deadline'
        OTHER = 'other', 'Other'

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=20, choices=Type.choices)
    event_date = models.DateTimeField('event date/time')
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['event_date', 'pk']

    def __str__(self):
        return f'{self.get_event_type_display()} - {self.application}'


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='notification')
    title = models.CharField(max_length=100)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at', '-pk']


class SecurityQuestion(models.Model):
    class Question(models.TextChoices):
        PET = 'pet', 'What was the name of your first pet?'
        CITY = 'city', 'What city were you born in?'
        SCHOOL = 'school', 'What was the name of your elementary school?'
        FRIEND = 'friend', 'What was the name of your childhood best friend?'
        CAR = 'car', 'What was the make/model of your first car?'

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='security_question')
    question = models.CharField(max_length=20, choices=Question.choices)
    answer_hash = models.CharField(max_length=128)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def normalize_answer(answer):
        return ' '.join(answer.split()).casefold()

    def set_answer(self, answer):
        self.answer_hash = make_password(self.normalize_answer(answer))

    def check_answer(self, answer):
        return check_password(self.normalize_answer(answer), self.answer_hash)
