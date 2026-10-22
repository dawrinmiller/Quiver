from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError
from django.utils import timezone

from tracker.email_reminders import (
    eligible_events,
    expire_exhausted_claims,
    send_event_reminder,
    site_url
)


# Run email reminders from the command line or a scheduled task.
class Command(BaseCommand):
    help = 'Send opted-in email reminders for events within the next 24 hours.'

    # Check the settings, process reminders, and print the totals.
    def handle(self, *args, **options):
        # Stop before sending anything if the website address is invalid.
        try:
            base_url = site_url()
        except ImproperlyConfigured as error:
            raise CommandError(str(error))
        # Track the result of each reminder checked during this run.
        counts = {
            'sent': 0,
            'skipped': 0,
            'failed': 0
        }
        counts['failed'] += expire_exhausted_claims(timezone.now())
        event_ids = list(
            eligible_events(timezone.now()).values_list(
                'pk',
                flat=True
            )
        )
        # Process each eligible event without changing in-app notifications.
        for event_id in event_ids:
            try:
                result = send_event_reminder(event_id, base_url)
            except OperationalError:
                # SQLite can be briefly busy. Leave records for the next run.
                self.stderr.write('Database unavailable for a reminder; check again on the next run.')
                result = 'failed'
            counts[result] += 1
        # Print totals without showing private account information.
        self.stdout.write('Email reminders processed:')
        for status, count in counts.items():
            self.stdout.write(f'- {status.title()}: {count}')
