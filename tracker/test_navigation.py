from html.parser import HTMLParser
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Application, Event, Notification


# Read the page links so the tests can check the navigation.
class NavigationMarkup(HTMLParser):
    # Set up the values this object needs.
    def __init__(self, html):
        super().__init__()
        self.in_navigation = False
        self.in_header = False
        self.links = []
        self.header_links = []
        self.feed(html)

    # Collect navigation links while reading the page.
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'header':
            self.in_header = True
        if tag == 'nav' and attrs.get('id') == 'main-navigation':
            self.in_navigation = True
        if tag == 'a':
            if self.in_navigation:
                self.links.append(attrs)
            if self.in_header:
                self.header_links.append(attrs.get('href'))

    # Stop collecting links when their section ends.
    def handle_endtag(self, tag):
        if tag == 'nav':
            self.in_navigation = False
        if tag == 'header':
            self.in_header = False


# Check that navigation links, page highlights, and badges still work.
class NavigationTests(TestCase):
    # Create the sample account and records used by these tests.
    def setUp(self):
        self.user = get_user_model().objects.create_user('navigation')
        self.client.force_login(self.user)
        self.destinations = [
            'notifications',
            'new_application',
            'dashboard',
            'upcoming_events',
            'application_board',
            'job_search_calendar'
        ]

    # Check destinations order active page and header controls.
    def test_destinations_order_active_page_and_header_controls(self):
        for destination in self.destinations:
            response = self.client.get(reverse(destination))
            self.assertEqual(response.status_code, 200)
            markup = NavigationMarkup(response.content.decode())
            self.assertEqual(
                [link['href'] for link in markup.links],
                [reverse(name) for name in self.destinations]
            )
            self.assertEqual(
                [link['href'] for link in markup.links if link.get('aria-current') == 'page'],
                [reverse(destination)]
            )
            self.assertEqual(
                markup.header_links,
                [reverse('dashboard'), reverse('new_application'), reverse('profile')]
            )
            self.assertContains(response, 'id="navigation-toggle"')
            self.assertContains(response, 'aria-controls="main-navigation"')
            self.assertContains(response, reverse('logout'))
            self.assertContains(response, 'QuiverLightLogo.png')

    # Check unread badge uses existing notifications.
    def test_unread_badge_uses_existing_notifications(self):
        app = Application.objects.create(
            user=self.user,
            company='Example',
            role='Engineer'
        )
        event = Event.objects.create(
            application=app,
            event_type='interview',
            event_date=timezone.now()+timedelta(hours=1)
        )
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'aria-label="1 unread notifications"')
        notification = Notification.objects.get(event=event)
        self.client.post(reverse('notification_read', args=[notification.pk]))
        self.assertContains(
            self.client.get(reverse('dashboard')),
            'aria-label="0 unread notifications"'
        )

    # Check application and calendar subpages highlight parent.
    def test_application_and_calendar_subpages_highlight_parent(self):
        app = Application.objects.create(
            user=self.user,
            company='Example',
            role='Engineer'
        )
        for url, parent in [
            (reverse('application_detail', args=[app.pk]), 'dashboard'),
            (reverse('calendar_event_create'), 'job_search_calendar')
        ]:
            response = self.client.get(url)
            markup = NavigationMarkup(response.content.decode())
            self.assertEqual(
                [link['href'] for link in markup.links if link.get('aria-current') == 'page'],
                [reverse(parent)]
            )

    # Check anonymous pages do not expose protected navigation.
    def test_anonymous_pages_do_not_expose_protected_navigation(self):
        self.client.logout()
        for name in ['login', 'register', 'password_recovery', 'email_password_reset']:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, 'id="main-navigation"')
            self.assertNotContains(response, 'id="navigation-toggle"')
