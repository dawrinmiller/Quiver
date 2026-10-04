import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


# Describe the first set of database changes for the tracker.
class Migration(migrations.Migration):

    # Identify this as the tracker's first database setup.
    initial = True

    # Make sure the user-account tables exist before adding tracker records.
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    # Create the application and event tables used to save tracking information.
    operations = [
        migrations.CreateModel(
            name='Application',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('company', models.CharField(max_length=200)),
                ('role', models.CharField(max_length=200, verbose_name='job title')),
                ('date_submitted', models.DateField()),
                ('job_description', models.TextField(blank=True)),
                ('job_link', models.URLField(blank=True)),
                ('status', models.CharField(choices=[('applied', 'Applied'), ('interviewing', 'Interviewing'), ('offer', 'Offer'), ('rejected', 'Rejected')], default='applied', max_length=20)),
                ('notes', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='applications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-date_submitted', '-pk'],
            },
        ),
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_type', models.CharField(choices=[('interview', 'Interview'), ('follow_up', 'Follow-up'), ('deadline', 'Application deadline'), ('other', 'Other')], max_length=20)),
                ('event_date', models.DateTimeField(verbose_name='event date/time')),
                ('description', models.TextField(blank=True)),
                ('reminder_sent', models.BooleanField(default=False)),
                ('application', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='tracker.application')),
            ],
            options={
                'ordering': ['event_date', 'pk'],
            },
        ),
    ]
