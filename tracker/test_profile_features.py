from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import ApplicationForm, DocumentForm
from .health import calculate_health
from .models import Application, Document, Event, Notification, Profile


# Exercise private documents and profile data using temporary storage.
class ProfileFeatureTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.user = get_user_model().objects.create_user('owner')
        self.other = get_user_model().objects.create_user('other')
        self.client.force_login(self.user)

    def upload(self, kind='resume'):
        response = self.client.post(
            reverse('profile'),
            {
                'action': 'document',
                'kind': kind,
                'file': SimpleUploadedFile('career.pdf', b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF')
            }
        )
        self.assertRedirects(response, reverse('profile'))
        return Document.objects.filter(user=self.user, kind=kind).latest('pk')

    def test_upload_select_download_delete_and_null_references(self):
        resume = self.upload()
        cover = self.upload('cover_letter')
        data = {
            'company': 'Google',
            'role': 'Data Analyst',
            'status': 'preparing',
            'resume': resume.pk,
            'cover_letter': cover.pk
        }
        self.assertEqual(
            self.client.post(reverse('new_application'), data).status_code,
            302
        )
        application = Application.objects.get(user=self.user)
        self.assertEqual(application.resume, resume)
        self.assertEqual(application.cover_letter, cover)
        self.assertEqual(Document.objects.count(), 2)
        self.assertContains(
            self.client.get(reverse('application_detail', args=[application.pk])),
            'career.pdf'
        )
        for document in (resume, cover):
            response = self.client.get(reverse('document_download', args=[document.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertIn('attachment', response['Content-Disposition'])
            response.close()
        response = self.client.get(reverse('document_download', args=[resume.pk]), {'view': '1'})
        self.assertIn('inline', response['Content-Disposition'])
        response.close()
        self.assertEqual(
            self.client.get(reverse('document_delete', args=[resume.pk])).status_code,
            405
        )
        storage, name = resume.file.storage, resume.file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('document_delete', args=[resume.pk]))
        self.assertFalse(storage.exists(name))
        application.refresh_from_db()
        self.assertIsNone(application.resume)
        self.assertEqual(
            self.client.get(reverse('application_detail', args=[application.pk])).status_code,
            200
        )

    def test_document_ownership_authentication_and_csrf(self):
        document = self.upload()
        self.client.force_login(self.other)
        for route in ('document_download', 'document_delete'):
            self.assertEqual(
                self.client.post(reverse(route, args=[document.pk])).status_code,
                404
            )
        self.assertEqual(
            self.client.get(reverse('document_download', args=[document.pk])).status_code,
            404
        )
        form = ApplicationForm(
            {
                'company': 'X',
                'role': 'Y',
                'status': 'preparing',
                'resume': document.pk
            },
            user=self.other
        )
        self.assertFalse(form.is_valid())
        self.assertIn('resume', form.errors)
        self.assertNotContains(self.client.get(reverse('profile')), 'career.pdf')
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(
            client.post(reverse('document_delete', args=[document.pk])).status_code,
            403
        )
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse('document_download', args=[document.pk])).status_code,
            302
        )

    def test_upload_validation(self):
        for name, content in [('fake.pdf', b'not a pdf'), ('fake.doc', b'not OLE'), ('fake.docx', b'not ZIP'),
            ('script.html', b'<script>bad</script>'), ('big.pdf', b'x' * (5 * 1024 * 1024 + 1))]:
            form = DocumentForm({'kind': 'resume'}, {'file': SimpleUploadedFile(name, content)})
            self.assertFalse(form.is_valid(), name)
            self.assertIn('file', form.errors)
        data = BytesIO()
        with ZipFile(data, 'w') as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('word/document.xml', '<document/>')
        form = DocumentForm(
            {'kind': 'cover_letter'},
            {
                'file': SimpleUploadedFile('letter.docx', data.getvalue())
            }
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_biography_linkedin_edit_clear_and_privacy(self):
        self.client.get(reverse('profile'))
        self.assertFalse(Profile.objects.filter(user=self.user).exists())
        data = {
            'action': 'profile',
            'biography': '<script>skills</script>\nGoals',
            'linkedin_url': 'https://www.linkedin.com/in/student/'
        }
        self.assertRedirects(self.client.post(reverse('profile'), data), reverse('profile'))
        response = self.client.get(reverse('profile'))
        self.assertContains(response, '&lt;script&gt;skills&lt;/script&gt;')
        self.assertContains(response, 'rel="noopener noreferrer"')
        for url in (
            'http://linkedin.com/in/student',
            'https://linkedin.com.evil.com/in/student',
            'https://www.linkedin.com/jobs',
            'https://evil.com/in/student'
        ):
            self.assertContains(
                self.client.post(reverse('profile'), {**data, 'linkedin_url': url}),
                'Use a LinkedIn profile URL'
            )
        self.client.post(
            reverse('profile'),
            {
                **data,
                'biography': 'Updated biography',
                'linkedin_url': ''
            }
        )
        profile = Profile.objects.get(user=self.user)
        self.assertEqual(profile.biography, 'Updated biography')
        self.assertEqual(profile.linkedin_url, '')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('profile')), 'Updated biography')

    def test_status_dates_feedback_and_statistics(self):
        data = {'company': 'Company', 'role': 'Engineer', 'status': 'preparing'}
        self.client.post(reverse('new_application'), data)
        app = Application.objects.get(user=self.user)
        self.assertIsNone(app.date_submitted)
        self.assertEqual(calculate_health(self.user.applications.all())['submitted'], 0)
        url = reverse('application_update', args=[app.pk])
        self.assertContains(
            self.client.post(url, {**data, 'status': 'applied'}),
            'Enter the actual submission date'
        )
        data['date_submitted'] = str(timezone.localdate())
        for status in (
            'applied',
            'online_interview',
            'onsite_interview',
            'interviewing',
            'offer',
            'rejected'
        ):
            self.assertEqual(
                self.client.post(url, {**data, 'status': status}).status_code,
                302
            )
            dashboard = self.client.get(reverse('dashboard'))
            self.assertEqual(
                dashboard.context['interviewing_count'],
                int(
                    status in ('online_interview', 'onsite_interview', 'interviewing')
                )
            )
        app.refresh_from_db()
        self.assertTrue(app.reached_interview)
        for text in ('Need SQL experience', 'More interview detail'):
            self.client.post(url, {**data, 'status': 'rejected', 'rejection_feedback': text})
            self.assertContains(
                self.client.get(reverse('application_detail', args=[app.pk])),
                text
            )
            self.assertContains(self.client.get(reverse('profile')), text)
        self.client.post(
            url,
            {
                **data,
                'status': 'applied',
                'rejection_feedback': 'More interview detail'
            }
        )
        self.assertContains(self.client.get(url), 'More interview detail')
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse('profile')), 'More interview detail')
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.user)
        self.client.post(url, {**data, 'status': 'rejected', 'rejection_feedback': ''})
        self.assertFalse(self.user.applications.get().rejection_feedback)

    def test_notifications_disappear_and_stay_read(self):
        app = Application.objects.create(user=self.user, company='Test', role='Intern')
        event = Event.objects.create(
            application=app,
            event_type='other',
            event_date=timezone.now() + timedelta(days=1)
        )
        notification = Notification.objects.create(
            user=self.user,
            event=event,
            title='Unique reminder',
            message='A reminder'
        )
        response = self.client.get(reverse('notifications'))
        self.assertIn(notification, response.context['notifications'])
        self.client.post(reverse('notification_read', args=[notification.pk]))
        for _ in range(2):
            response = self.client.get(reverse('notifications'))
            self.assertNotIn(notification, response.context['notifications'])
            self.assertEqual(response.context['unread_notification_count'], 0)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_dashboard_has_all_choices_rows_and_overall_totals(self):
        for status in Application.Status.values:
            Application.objects.create(
                user=self.user,
                company='Google',
                role='Data Analyst',
                status=status
            )
        Application.objects.create(user=self.other, company='Private employer', role='Hidden role')
        response = self.client.get(reverse('dashboard'), {'q': 'goo'})
        self.assertEqual(
            response.context['applications'].count(),
            len(Application.Status.values)
        )
        self.assertEqual(response.context['interviewing_count'], 3)
        self.assertEqual(response.context['applied_count'], 1)
        self.assertEqual(response.context['offers_count'], 1)
        self.assertEqual(response.context['rejected_count'], 1)
        self.assertNotContains(response, 'Private employer')
        for value, label in Application.Status.choices:
            self.assertContains(
                response,
                f'<option value="{value}">{label}</option>',
                html=True
            )
        self.assertContains(response, 'id="application-search"')
        self.assertContains(response, 'tracker/js/dashboard.js')


# Check recommendations and scoring evidence across small and large samples.
class HealthTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('health')

    def add(self, status='applied', **kwargs):
        return Application.objects.create(
            user=self.user,
            company='Test',
            role='Intern',
            status=status,
            date_submitted=timezone.localdate(),
            **kwargs
        )

    def health(self):
        result = calculate_health(self.user.applications.all())
        self.assertGreaterEqual(result['score'], 0)
        self.assertLessEqual(result['score'], 100)
        return result

    def test_empty_preparing_and_small_samples(self):
        self.assertEqual(self.health()['score'], 0)
        self.add('preparing')
        self.assertEqual(self.health()['submitted'], 0)
        self.add('offer')
        result = self.health()
        self.assertEqual(result['interviews'], 0)
        self.assertLess(result['score'], 100)
        self.assertIn('small', ' '.join(result['recommendations']))

    def test_many_applications_few_interviews_and_feedback(self):
        for _ in range(12):
            self.add('rejected', rejection_feedback='Employer feedback')
        result = self.health()
        self.assertIn('Tailor your resume', ' '.join(result['recommendations']))
        self.assertIn('saved employer feedback', ' '.join(result['recommendations']))

    def test_interview_history_events_offers_and_mixed_totals(self):
        for _ in range(3):
            app = self.add('online_interview')
            app.status = 'rejected'
            app.save()
        self.assertIn('mock interviews', ' '.join(self.health()['recommendations']))
        app = self.add('offer')
        Event.objects.create(
            application=app,
            event_type='interview',
            event_date=timezone.now() - timedelta(days=1)
        )
        Event.objects.create(
            application=app,
            event_type='interview',
            event_date=timezone.now() - timedelta(hours=1)
        )
        self.add('offer')  # No invented interview history for this legacy offer.
        self.add('onsite_interview')
        self.add('interviewing')
        self.add('preparing')
        result = self.health()
        self.assertEqual(result['submitted'], 7)
        self.assertEqual(result['interviews'], 6)
        self.assertEqual(result['offers'], 2)
        self.assertEqual(result['rejected'], 3)
        self.assertEqual(result['in_progress'], 2)
        self.assertIn('Maintain your routine', ' '.join(result['recommendations']))

    def test_inactive_and_pending_are_not_failed(self):
        for _ in range(10):
            self.add()
        result = self.health()
        self.assertNotIn('Few applications', ' '.join(result['recommendations']))
        self.user.applications.update(date_submitted=timezone.localdate() - timedelta(days=40))
        self.assertIn(
            'weekly application goal',
            ' '.join(self.health()['recommendations'])
        )
