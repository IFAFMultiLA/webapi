"""
Automated tests.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.conf import settings
from django.forms.models import ModelFormMetaclass
from django.template.response import TemplateResponse
from django.test import TestCase, RequestFactory
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from .models import max_options_length, current_time_bytes, Application, ApplicationConfig, ApplicationSession, \
    UserApplicationSession, User, TrackingSession, TrackingEvent
from .admin import admin_site, ApplicationAdmin, ApplicationConfigAdmin, ApplicationSessionAdmin, \
    TrackingSessionAdmin, TrackingEventAdmin


# ----- helper functions -----


def tznow():
    return datetime.now(ZoneInfo(settings.TIME_ZONE))


# ----- views -----


class CustomAPIClient(APIClient):
    """
    Extended test client based on APIClient.
    """
    def post_json(self, path, data=None, follow=False, **extra):
        """
        POST request in JSON format.
        """
        return self.post(path, data=data, format='json', follow=follow, **extra)

    def post(self, path, data=None, format=None, content_type=None, follow=False, **extra):
        """POST request with optional "extras", i.e. CSRF token / auth token."""
        self._handle_extra(extra)
        return super().post(path, data=data, format=format, content_type=content_type, follow=follow, **extra)

    def get(self, path, data=None, follow=False, **extra):
        """GET request with optional "extras", i.e. CSRF token / auth token."""
        self._handle_extra(extra)
        return super().get(path, data=data, follow=follow, **extra)

    def _handle_extra(self, extra):
        """Handle extras, i.e. CSRF token / auth token."""
        omit_csrftoken = extra.pop('omit_csrftoken', False)
        if 'csrftoken' in self.cookies and not omit_csrftoken:
            extra['HTTP_X_CSRFTOKEN'] = self.cookies['csrftoken'].value

        auth_token = extra.pop('auth_token', None)
        if auth_token:
            extra['HTTP_AUTHORIZATION'] = f'Token {auth_token}'


class CustomAPITestCase(APITestCase):
    """
    Extended test case using CustomAPIClient.
    """
    client_class = CustomAPIClient


class ModelsCommonTests(TestCase):
    """
    Test case for some common functions in models module.
    """
    def test_max_options_length(self):
        self.assertIs(max_options_length([]), 0)
        self.assertIs(max_options_length([('', '')]), 0)
        self.assertIs(max_options_length([('', 'x')]), 0)
        self.assertIs(max_options_length([('x', 'y')]), 1)
        self.assertIs(max_options_length([('x', 'yz')]), 1)
        self.assertIs(max_options_length([('xz', 'yz')]), 2)
        self.assertIs(max_options_length([('xz', 'y')]), 2)
        self.assertIs(max_options_length([('xz', 'y')]), 2)
        self.assertIs(max_options_length([('xz', 'y'), ('foo', 'bar')]), 3)
        self.assertIs(max_options_length([('xz', 'y'), ('foo', 'bar'), ('foobar', '')]), 6)

    def test_current_time_bytes(self):
        self.assertIsInstance(current_time_bytes(), bytes)
        self.assertTrue(tznow() - datetime.fromtimestamp(float.fromhex(current_time_bytes().decode('utf-8')),
                                                         ZoneInfo(settings.TIME_ZONE))
                        < timedelta(seconds=1))


class ApplicationSessionModelTests(TestCase):
    """
    Test case for ApplicationSession model.
    """
    def setUp(self):
        # create an app for an app config
        app = Application.objects.create(name='test app', url='https://test.app')
        # create an app config for an app session
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})
        # create an app session
        app_sess = ApplicationSession(config=app_config, auth_mode='none')

        # creating a session code is necessary before saving
        self.code = app_sess.generate_code()
        app_sess.save()

    def test_generate_code(self):
        app_config = ApplicationConfig.objects.get(label='test config')
        app_sess = ApplicationSession(config=app_config, auth_mode='none')

        code = app_sess.generate_code()
        self._check_code(app_sess, code)

        with self.assertRaisesRegex(ValueError, r'^`self.code` is already given'):
            app_sess.generate_code()

        code2 = app_sess.generate_code(force=True)
        self._check_code(app_sess, code2)
        self.assertTrue(code != code2)

        app_sess.save()

        app_sess_no_config = ApplicationSession(auth_mode='none')
        with self.assertRaisesRegex(ValueError, r'^`self.config` must be set'):
            app_sess_no_config.generate_code()

    def test_session_url(self):
        # retrieve app session stored in `setUp`
        app_sess = ApplicationSession.objects.get(code=self.code)
        sess_url = app_sess.session_url()
        self.assertTrue(sess_url.startswith(app_sess.config.application.url))
        self.assertTrue(sess_url.endswith(f'?sess={app_sess.code}'))

    def _check_code(self, app_sess, code):
        self.assertIs(code, app_sess.code)
        self.assertIsInstance(code, str)
        self.assertTrue(len(code) == 10)


class UserApplicationSessionModelTests(TestCase):
    """
    Test case for UserApplicationSession model.
    """
    def test_generate_code(self):
        # create all models that are necessary for a user application session
        app = Application(name='test app', url='https://test.app')
        app_config = ApplicationConfig(application=app, label='test config', config={'test': True})
        app_sess = ApplicationSession(config=app_config, auth_mode='none')
        app_sess.generate_code()
        app.save()
        app_config.save()
        app_sess.save()

        user_app_sess = UserApplicationSession(application_session=app_sess)

        code = user_app_sess.generate_code()
        self._check_code(user_app_sess, code)

        with self.assertRaisesRegex(ValueError, r'^`self.code` is already given'):
            user_app_sess.generate_code()

        code2 = user_app_sess.generate_code(force=True)
        self._check_code(user_app_sess, code2)
        self.assertTrue(code != code2)

        user_app_sess.save()

    def _check_code(self, user_app_sess, code):
        self.assertIs(code, user_app_sess.code)
        self.assertIsInstance(code, str)
        self.assertTrue(len(code) == 64)


class ViewTests(CustomAPITestCase):
    """
    Test case for views.
    """
    def setUp(self):
        # create a test user
        self.user_password = 'testpw'
        self.user = User.objects.create_user('testuser', email='testuser@testserver.com', password=self.user_password)

        # create a test app
        app = Application.objects.create(name='test app', url='https://test.app')

        # create a config for this app
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})

        # create an app session w/o authentication
        self.app_sess_no_auth = ApplicationSession(config=app_config, auth_mode='none')
        self.app_sess_no_auth.generate_code()
        self.app_sess_no_auth.save()

        # create an app session that requires login
        self.app_sess_login = ApplicationSession(config=app_config, auth_mode='login')
        self.app_sess_login.generate_code()
        self.app_sess_login.save()

        # create a second test app
        self.app_with_default_sess = Application.objects.create(name='test app 2', url='https://test2.app')

        # create a config for this app
        app_config = ApplicationConfig.objects.create(application=self.app_with_default_sess, label='test config 2',
                                                      config={})

        # create an app session w/o authentication
        self.app_sess_no_auth2 = ApplicationSession(config=app_config, auth_mode='none')
        self.app_sess_no_auth2.generate_code()
        self.app_sess_no_auth2.save()

        # set this app session as default for this app
        self.app_with_default_sess.default_application_session = self.app_sess_no_auth2
        self.app_with_default_sess.save()

    def test_404(self):
        response = self.client.get('foo', content_type='application/json')
        self.assertEqual(response.headers['Content-Type'], 'application/json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        response = self.client.get('foo')
        self.assertTrue(response.headers['Content-Type'].startswith('text/html'))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_app_session(self):
        url = reverse('session')

        # test no_auth app session
        valid_data = {'sess': self.app_sess_no_auth.code}

        # failures
        self.assertEqual(self.client.post(url, valid_data).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_400_BAD_REQUEST)                 # missing data
        self.assertEqual(self.client.get(url, {'sess': 'foo'}).status_code, status.HTTP_404_NOT_FOUND)  # wrong sess ID

        # OK
        response = self.client.get(url, valid_data)
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user_app_sess = UserApplicationSession.objects.get(application_session=self.app_sess_no_auth)
        self.assertIsNone(user_app_sess.user)
        self.assertIsInstance(user_app_sess.code, str)
        self.assertTrue(len(user_app_sess.code) == 64)
        self.assertEqual(response.json(), {
            'sess_code': valid_data['sess'],
            'auth_mode': 'none',
            'user_code': user_app_sess.code,
            'config': self.app_sess_no_auth.config.config
        })

        # test login app session
        valid_data = {'sess': self.app_sess_login.code}

        response = self.client.get(url, valid_data)
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(response.status_code, status.HTTP_200_OK)   # doesn't create a user session
        self.assertEqual(UserApplicationSession.objects.filter(application_session=self.app_sess_login).count(), 0)
        self.assertEqual(response.json(), {
            'sess_code': valid_data['sess'],
            'auth_mode': 'login'
        })

        # test default app session – OK
        response = self.client.get(url, HTTP_REFERER=self.app_with_default_sess.url)
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(response.status_code, status.HTTP_200_OK)   # doesn't create a user session
        self.assertEqual(response.json(), {
            'sess_code': self.app_sess_no_auth2.code,
        })

        # test default app session – OK too
        response = self.client.get(url, HTTP_REFERER=self.app_with_default_sess.url + '/')
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(response.status_code, status.HTTP_200_OK)   # doesn't create a user session
        self.assertEqual(response.json(), {
            'sess_code': self.app_sess_no_auth2.code,
        })

        # test default app session – OK too
        response = self.client.get(url, {'referrer': self.app_with_default_sess.url})
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(response.status_code, status.HTTP_200_OK)   # doesn't create a user session
        self.assertEqual(response.json(), {
            'sess_code': self.app_sess_no_auth2.code,
        })

        # test default app session – fail
        self.assertEqual(self.client.get(url, HTTP_REFERER='http://foobar.localhost').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_app_session_login(self):
        # request application session – also sets CSRF token in cookie
        self.client.get(reverse('session'), {'sess': self.app_sess_login.code})

        # test application session login
        self.client.handler.enforce_csrf_checks = True
        valid_data = {'sess': self.app_sess_login.code, 'username': self.user.username, 'password': self.user_password}
        url = reverse('session_login')

        # failures
        self.assertEqual(self.client.get(url, data=valid_data).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.post_json(url, data={}).status_code, status.HTTP_400_BAD_REQUEST)  # no data
        # wrong application session
        self.assertEqual(self.client.post_json(url, data={
            'sess': 'foo', 'username': self.user.username, 'password': self.user_password
        }).status_code, status.HTTP_404_NOT_FOUND)
        # application session that doesn't require login
        self.assertEqual(self.client.post_json(url, data={
            'sess': self.app_sess_no_auth.code, 'username': self.user.username, 'password': self.user_password
        }).status_code, status.HTTP_400_BAD_REQUEST)
        # wrong username
        self.assertEqual(self.client.post_json(url, data={
            'sess': self.app_sess_login.code, 'username': 'foo', 'password': self.user_password
        }).status_code, status.HTTP_404_NOT_FOUND)
        # wrong email
        self.assertEqual(self.client.post_json(url, data={
            'sess': self.app_sess_login.code, 'email': 'foo', 'password': self.user_password
        }).status_code, status.HTTP_404_NOT_FOUND)
        # wrong password
        self.assertEqual(self.client.post_json(url, data={
            'sess': self.app_sess_login.code, 'username': self.user.username, 'password': 'foo'
        }).status_code, status.HTTP_401_UNAUTHORIZED)
        # no CSRF token
        # self.assertEqual(self.client.post_json(url, data=valid_data, omit_csrftoken=True).status_code,
        #                  status.HTTP_401_UNAUTHORIZED)

        # OK with username / password
        response = self.client.post_json(url, data=valid_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user_app_sess = UserApplicationSession.objects.filter(application_session=self.app_sess_login).latest('created')
        self.assertEqual(user_app_sess.user.id, self.user.id)
        self.assertIsInstance(user_app_sess.code, str)
        self.assertTrue(len(user_app_sess.code) == 64)
        self.assertEqual(response.json(), {
            'sess_code': valid_data['sess'],
            'auth_mode': 'login',
            'user_code': user_app_sess.code,
            'config': self.app_sess_no_auth.config.config
        })

        # OK with email / password
        valid_data = {'sess': self.app_sess_login.code, 'email': self.user.email, 'password': self.user_password}

        response = self.client.post_json(url, data=valid_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user_app_sess = UserApplicationSession.objects.filter(application_session=self.app_sess_login).latest('created')
        self.assertEqual(user_app_sess.user.id, self.user.id)
        self.assertIsInstance(user_app_sess.code, str)
        self.assertTrue(len(user_app_sess.code) == 64)
        self.assertEqual(response.json(), {
            'sess_code': valid_data['sess'],
            'auth_mode': 'login',
            'user_code': user_app_sess.code,
            'config': self.app_sess_no_auth.config.config
        })

    def test_register_user(self):
        # test register user
        valid_data = {'username': 'testuser2', 'email': 'testuser2@localhost', 'password': 'testuser2pw'}
        valid_data_no_email = valid_data.copy()
        del valid_data_no_email['email']
        valid_data_no_user = valid_data.copy()
        del valid_data_no_user['username']
        url = reverse('register_user')

        # failures
        self.assertEqual(self.client.get(url, data=valid_data).status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(self.client.post_json(url, data={}).status_code, status.HTTP_400_BAD_REQUEST)  # no data
        # missing username and email
        self.assertEqual(self.client.post_json(url, data={'password': 'testuser2pw'}).status_code,
                         status.HTTP_400_BAD_REQUEST)
        # missing password
        self.assertEqual(self.client.post_json(url, data={
            'username': 'testuser2', 'email': 'testuser2@localhost'
        }).status_code, status.HTTP_400_BAD_REQUEST)
        # password too short
        response = self.client.post_json(url, data={
            'username': 'testuser2', 'email': 'testuser2@localhost', 'password': 'short'
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()['error'], 'pw_too_short')
        # password equals username
        response = self.client.post_json(url, data={
            'username': 'testuser2', 'email': 'testuser2@localhost', 'password': 'testuser2'
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()['error'], 'pw_same_as_user')
        # password equals email
        response = self.client.post_json(url, data={
            'username': 'testuser2', 'email': 'testuser2@localhost', 'password': 'testuser2@localhost'
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()['error'], 'pw_same_as_email')
        # email is not valid
        response = self.client.post_json(url, data={
            'username': 'testuser2', 'email': 'wrong', 'password': 'testuser2@localhost'
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()['error'], 'invalid_email')

        # OK with username, email, password
        response = self.client.post_json(url, data=valid_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username=valid_data['username'])
        self.assertEqual(user.email, valid_data['email'])
        self.assertTrue(user.check_password(valid_data['password']))
        user.delete()

        # OK with username, password
        response = self.client.post_json(url, data=valid_data_no_email)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username=valid_data['username'])
        self.assertEqual(user.email, '')
        self.assertTrue(user.check_password(valid_data['password']))
        user.delete()

        # OK with email, password
        response = self.client.post_json(url, data=valid_data_no_user)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(username=valid_data['email'])
        self.assertEqual(user.email, valid_data['email'])
        self.assertEqual(user.email, user.username)
        self.assertTrue(user.check_password(valid_data['password']))

    def test_register_user_twice(self):
        valid_data = {'username': 'testuser2', 'email': 'testuser2@localhost', 'password': 'testuser2pw'}
        url = reverse('register_user')

        # OK with username, email, password
        response = self.client.post_json(url, data=valid_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # failure with same data (user already exists)
        response = self.client.post_json(url, data=valid_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.json()['error'], 'user_already_registered')

    def test_start_tracking(self):
        # request application session – also sets CSRF token in cookie
        response = self.client.get(reverse('session'), {'sess': self.app_sess_no_auth.code})
        auth_token = response.json()['user_code']

        # test start tracking
        url = reverse('start_tracking')
        now = tznow()
        valid_data = {'sess': self.app_sess_no_auth.code, 'start_time': now.isoformat()}

        # failures
        self.assertEqual(self.client.get(url, data=valid_data, auth_token=auth_token).status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)  # wrong method
        self.assertEqual(self.client.post_json(url, data={}, auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # no data
        self.assertEqual(self.client.post_json(url, data={'sess': 'foo'}, auth_token=auth_token).status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong application session
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # missing start_time
        self.assertEqual(self.client.post_json(url, data=valid_data, auth_token='foo').status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong auth token

        response = self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                    'start_time': (now + timedelta(minutes=5)).isoformat()},
                                         auth_token=auth_token)   # start time is in future
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json(), {'validation_errors': {'start_time': ['invalid timestamp']}})

        response = self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                    'start_time': (now - timedelta(minutes=120)).isoformat()},
                                         auth_token=auth_token)  # start time is too long in past
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json(), {'validation_errors': {'start_time': ['invalid timestamp']}})

        # OK without device_info
        response = self.client.post_json(url, data=valid_data, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tracking_sess = TrackingSession.objects.get(user_app_session__code=auth_token)
        self.assertEqual(response.json(), {'tracking_session_id': tracking_sess.id})
        self.assertEqual(tracking_sess.start_time, now.astimezone(timezone.utc))

        # OK with repeated request -> return same tracking session
        response = self.client.post_json(url, data=valid_data, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tracking_sess2 = TrackingSession.objects.get(user_app_session__code=auth_token)
        self.assertEqual(response.json(), {'tracking_session_id': tracking_sess2.id})
        self.assertEqual(tracking_sess, tracking_sess2)

        # OK with device_info in new user session
        response = self.client.get(reverse('session'), {'sess': self.app_sess_no_auth.code})
        auth_token = response.json()['user_code']

        valid_data = {
            'sess': self.app_sess_no_auth.code,
            'start_time': now.isoformat(),
            'device_info': {'test key': 'test value'}
        }

        response = self.client.post_json(url, data=valid_data, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tracking_sess = TrackingSession.objects.get(user_app_session__code=auth_token)
        self.assertEqual(response.json(), {'tracking_session_id': tracking_sess.id})
        self.assertEqual(tracking_sess.start_time, now.astimezone(timezone.utc))
        self.assertEqual(tracking_sess.device_info, valid_data['device_info'])

    def test_stop_tracking(self):
        # request application session – also sets CSRF token in cookie
        response = self.client.get(reverse('session'), {'sess': self.app_sess_no_auth.code})
        auth_token = response.json()['user_code']

        # start tracking
        response = self.client.post_json(reverse('start_tracking'),
                                         data={'sess': self.app_sess_no_auth.code,
                                               'start_time': tznow().isoformat()},
                                         auth_token=auth_token)

        tracking_sess_id = response.json()['tracking_session_id']

        # test stop tracking
        url = reverse('stop_tracking')
        now = tznow()
        valid_data = {'sess': self.app_sess_no_auth.code,
                      'end_time': now.isoformat(),
                      'tracking_session_id': tracking_sess_id}

        # failures
        self.assertEqual(self.client.get(url, data=valid_data, auth_token=auth_token).status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)  # wrong method
        self.assertEqual(self.client.post_json(url, data={}, auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # no data
        self.assertEqual(self.client.post_json(url, data={'sess': 'foo'}, auth_token=auth_token).status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong application session
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                          'tracking_session_id': tracking_sess_id},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # missing end_time
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                          'end_time': now.isoformat()},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # missing tracking_session_id
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                          'end_time': now.isoformat(),
                                                          'tracking_session_id': 0},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # wrong tracking_session_id
        self.assertEqual(self.client.post_json(url, data=valid_data, auth_token='foo').status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong auth token
        response = self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                    'end_time': (now + timedelta(minutes=5)).isoformat(),
                                                    'tracking_session_id': tracking_sess_id},
                                         auth_token=auth_token)  # end time is in future
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json(), {'validation_errors': {'end_time': ['invalid timestamp']}})

        response = self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                    'end_time': (now - timedelta(minutes=120)).isoformat(),
                                                    'tracking_session_id': tracking_sess_id},
                                         auth_token=auth_token)    # end time is too long in past
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json(), {'validation_errors': {'end_time': ['invalid timestamp']}})

        # OK
        response = self.client.post_json(url, data=valid_data, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        tracking_sess = TrackingSession.objects.get(user_app_session__code=auth_token)
        self.assertEqual(response.json(), {'tracking_session_id': tracking_sess.id})
        self.assertEqual(tracking_sess.end_time, now.astimezone(timezone.utc))

        # failure with repeated request -> tracking session already ended
        self.assertEqual(self.client.post_json(url, data=valid_data, auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)

        # start tracking with same user session -> receive new tracking session ID
        now2 = tznow()
        response = self.client.post_json(reverse('start_tracking'),
                                         data={'sess': self.app_sess_no_auth.code,
                                               'start_time': now2.isoformat()},
                                         auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tracking_sess2 = TrackingSession.objects.get(user_app_session__code=auth_token, start_time=now2.isoformat())
        self.assertEqual(response.json(), {'tracking_session_id': tracking_sess2.id})
        self.assertTrue(tracking_sess.id != tracking_sess2.id)

    def test_track_event(self):
        # request application session – also sets CSRF token in cookie
        response = self.client.get(reverse('session'), {'sess': self.app_sess_no_auth.code})
        auth_token = response.json()['user_code']

        # start tracking
        response = self.client.post_json(reverse('start_tracking'),
                                         data={'sess': self.app_sess_no_auth.code,
                                               'start_time': tznow().isoformat()},
                                         auth_token=auth_token)

        tracking_sess_id = response.json()['tracking_session_id']

        # test tracking events
        url = reverse('track_event')
        now = tznow()
        test_event_no_val = {"time": now.isoformat(), "type": "testtype"}
        test_event_with_val = dict(**test_event_no_val, value={"testkey": "testvalue"})
        valid_data_no_val = {'sess': self.app_sess_no_auth.code,
                             'tracking_session_id': tracking_sess_id,
                             'event': test_event_no_val}
        valid_data_with_val = {'sess': self.app_sess_no_auth.code,
                               'tracking_session_id': tracking_sess_id,
                               'event': test_event_with_val}

        # failures
        self.assertEqual(self.client.get(url, data=valid_data_no_val, auth_token=auth_token).status_code,
                         status.HTTP_405_METHOD_NOT_ALLOWED)  # wrong method
        self.assertEqual(self.client.post_json(url, data={}, auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # no data
        self.assertEqual(self.client.post_json(url, data={'sess': 'foo'}, auth_token=auth_token).status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong application session
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                          'tracking_session_id': tracking_sess_id},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # missing event
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                          'event': test_event_no_val},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # missing tracking_session_id
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                          'event': test_event_no_val,
                                                          'tracking_session_id': 0},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # wrong tracking_session_id
        self.assertEqual(self.client.post_json(url, data=valid_data_no_val, auth_token='foo').status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong auth token

        response = self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                    'tracking_session_id': tracking_sess_id,
                                                    'event': {
                                                        "time": (now + timedelta(minutes=5)).isoformat(),
                                                        "type": "testtype"}
                                                    },
                                         auth_token=auth_token)  # time is in future
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json(), {'validation_errors': {'time': ['invalid timestamp']}})

        response = self.client.post_json(url, data={'sess': self.app_sess_no_auth.code,
                                                    'tracking_session_id': tracking_sess_id,
                                                    'event': {
                                                        "time": (now - timedelta(minutes=120)).isoformat(),
                                                        "type": "testtype"}
                                                    },
                                         auth_token=auth_token)  # time is too long in past
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json(), {'validation_errors': {'time': ['invalid timestamp']}})

        # OK without event value
        response = self.client.post_json(url, data=valid_data_no_val, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tracking_event = TrackingEvent.objects.get(tracking_session_id=tracking_sess_id)
        self.assertEqual(response.json(), {'tracking_event_id': tracking_event.id})
        self.assertEqual(tracking_event.time, now.astimezone(timezone.utc))
        self.assertEqual(tracking_event.type, test_event_no_val["type"])
        self.assertIsNone(tracking_event.value)

        # OK with event value
        response = self.client.post_json(url, data=valid_data_with_val, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tracking_event = TrackingEvent.objects.get(id=response.json()['tracking_event_id'])
        self.assertEqual(tracking_event.time, now.astimezone(timezone.utc))
        self.assertEqual(tracking_event.type, test_event_with_val["type"])
        self.assertEqual(tracking_event.value, test_event_with_val["value"])

        # stop tracking
        self.assertEqual(self.client.post_json(reverse('start_tracking'), data={
            'sess': self.app_sess_no_auth.code,
            'end_time': tznow().isoformat(),
            'tracking_session_id': tracking_sess_id
        }, auth_token=auth_token).status_code, status.HTTP_200_OK)


# ----- admin -----


class ModelAdminTests(TestCase):
    def _check_modeladmin_default_views(self, modeladm, view_args=None, check_views=None):
        view_args = view_args or {}
        check_views = check_views or ('add_view', 'change_view', 'changelist_view', 'delete_view', 'history_view')
        for viewfn_name in check_views:
            viewfn = getattr(modeladm, viewfn_name)
            args = view_args.get(viewfn_name, {})
            response = viewfn(self.request, **args)
            self.assertIsInstance(response, TemplateResponse)
            self.assertEqual(response.status_code, 200)

    @classmethod
    def setUpTestData(cls):
        user = User.objects.create_superuser(username="admin", email="admin@example.com", password="test")
        cls.request = RequestFactory().get('/admin')
        cls.request.user = user

    def test_application_admin(self):
        modeladm = ApplicationAdmin(Application, admin_site)

        app = Application(name="testapp", url="http://testapp.com")

        # check that "updated" and "updated_by" are correctly set
        form = modeladm.get_form(self.request, change=False)
        modeladm.save_model(self.request, app, form, change=False)
        self.assertEqual(app.updated_by, self.request.user)
        self.assertIsNotNone(app.updated)

        form = modeladm.get_form(self.request, app, change=True)
        modeladm.save_model(self.request, app, form, change=True)
        self.assertEqual(app.updated_by, self.request.user)
        self.assertIsNotNone(app.updated)

        # the instance in the DB is the same
        app_from_db = Application.objects.get(name="testapp")
        self.assertEqual(app_from_db.pk, app.pk)

        # check that default_application_session is not part of the form when creating a new application entry
        form = modeladm.get_form(self.request, obj=None)
        self.assertIsInstance(form, ModelFormMetaclass)
        self.assertNotIn('default_application_session', form.base_fields)

        # check that default_application_session is part of the form when update an existing application entry
        form = modeladm.get_form(self.request, obj=app)
        self.assertIsInstance(form, ModelFormMetaclass)
        self.assertIn('default_application_session', form.base_fields)

        obj_views_args = dict(object_id=str(app.pk))
        views_args = {'change_view': obj_views_args, 'delete_view': obj_views_args, 'history_view': obj_views_args}
        self._check_modeladmin_default_views(modeladm, views_args)

    def test_applicationconfig_admin(self):
        modeladm = ApplicationConfigAdmin(ApplicationConfig, admin_site)

        app = Application(name="testapp", url="http://testapp.com")
        app.save()
        appconfig = ApplicationConfig(application=app,
                                      label="testconfig",
                                      config={"key": "value"})
        del app

        # check that "updated" and "updated_by" are correctly set
        form = modeladm.get_form(self.request, change=False)
        modeladm.save_model(self.request, appconfig, form, change=False)
        self.assertEqual(appconfig.updated_by, self.request.user)
        self.assertIsNotNone(appconfig.updated)

        form = modeladm.get_form(self.request, appconfig, change=True)
        modeladm.save_model(self.request, appconfig, form, change=True)
        self.assertEqual(appconfig.updated_by, self.request.user)
        self.assertIsNotNone(appconfig.updated)

        # the instance in the DB is the same
        appconfig_from_db = ApplicationConfig.objects.get(label="testconfig")
        self.assertEqual(appconfig_from_db.pk, appconfig.pk)

        obj_views_args = dict(object_id=str(appconfig.pk))
        views_args = {'change_view': obj_views_args, 'delete_view': obj_views_args, 'history_view': obj_views_args}
        self._check_modeladmin_default_views(modeladm, views_args)

    def test_applicationsession_admin(self):
        modeladm = ApplicationSessionAdmin(ApplicationSession, admin_site)

        app = Application(name="testapp", url="http://testapp.com", updated_by=self.request.user)
        app.save()
        appconfig = ApplicationConfig(application=app,
                                      label="testconfig",
                                      config={"key": "value"},
                                      updated_by=self.request.user)
        appconfig.save()
        appsess = ApplicationSession(config=appconfig, auth_mode='none')
        del app

        # check that a session code is generated
        form = modeladm.get_form(self.request, change=False)
        modeladm.save_model(self.request, appsess, form, change=False)
        self.assertIsInstance(appsess.code, str)
        self.assertEqual(len(appsess.code), 10)
        self.assertEqual(appsess.updated_by, self.request.user)
        self.assertIsNotNone(appsess.updated)

        # check that "updated" and "updated_by" are correctly set on update
        form = modeladm.get_form(self.request, appsess, change=True)
        modeladm.save_model(self.request, appsess, form, change=True)
        self.assertEqual(appsess.updated_by, self.request.user)
        self.assertIsNotNone(appsess.updated)

        appsess_from_db = ApplicationSession.objects.get(config=appconfig)
        self.assertEqual(appsess_from_db.pk, appsess.pk)

        obj_views_args = dict(object_id=str(appsess.pk))
        views_args = {'change_view': obj_views_args, 'delete_view': obj_views_args, 'history_view': obj_views_args}
        self._check_modeladmin_default_views(modeladm, views_args)

    def test_trackingsession_admin(self):
        modeladm = TrackingSessionAdmin(TrackingSession, admin_site)

        self._check_modeladmin_default_views(modeladm, check_views=['changelist_view'])

    def test_trackingevent_admin(self):
        modeladm = TrackingEventAdmin(TrackingEvent, admin_site)

        self._check_modeladmin_default_views(modeladm, check_views=['changelist_view'])
