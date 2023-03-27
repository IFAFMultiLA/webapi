from datetime import datetime, timedelta, tzinfo, timezone

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from .models import max_options_length, current_time_bytes, Application, ApplicationConfig, ApplicationSession, \
    UserApplicationSession, User, TrackingSession


class CustomAPIClient(APIClient):
    def post_json(self, path, data=None, follow=False, **extra):
        omit_csrftoken = extra.pop('omit_csrftoken', False)
        if 'csrftoken' in self.cookies and not omit_csrftoken:
            extra['HTTP_X_CSRFTOKEN'] = self.cookies['csrftoken'].value

        auth_token = extra.pop('auth_token', None)
        if auth_token:
            extra['HTTP_AUTHORIZATION'] = f'Token {auth_token}'

        return self.post(path, data=data, format='json', follow=follow, **extra)


class CustomAPITestCase(APITestCase):
    client_class = CustomAPIClient


class ModelsCommonTests(TestCase):
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
        self.assertTrue(datetime.now() - datetime.fromtimestamp(float.fromhex(current_time_bytes().decode('utf-8')))
                        < timedelta(seconds=1))


class ApplicationSessionTests(TestCase):
    def setUp(self):
        app = Application.objects.create(name='test app', url='https://test.app')
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})
        app_sess = ApplicationSession(config=app_config, auth_mode='none')
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
        app_sess = ApplicationSession.objects.get(code=self.code)
        sess_url = app_sess.session_url()
        self.assertTrue(sess_url.startswith(app_sess.config.application.url))
        self.assertTrue(sess_url.endswith(f'?sess={app_sess.code}'))

    def _check_code(self, app_sess, code):
        self.assertIs(code, app_sess.code)
        self.assertIsInstance(code, str)
        self.assertTrue(len(code) == 10)


class UserApplicationSessionTests(TestCase):
    def test_generate_code(self):
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
    def setUp(self):
        self.user_password = 'testpw'
        self.user = User.objects.create_user('testuser', password=self.user_password)
        app = Application.objects.create(name='test app', url='https://test.app')
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})
        self.app_sess_no_auth = ApplicationSession(config=app_config, auth_mode='none')
        self.app_sess_no_auth.generate_code()
        self.app_sess_no_auth.save()
        self.app_sess_login = ApplicationSession(config=app_config, auth_mode='login')
        self.app_sess_login.generate_code()
        self.app_sess_login.save()

    def test_app_session(self):
        url = reverse('session')

        # test no_auth app session
        valid_data = {'sess': self.app_sess_no_auth.code}

        # failures
        self.assertEqual(self.client.post(url, valid_data).status_code, status.HTTP_400_BAD_REQUEST)    # wrong method
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

    def test_app_session_login(self):
        # request application session – also sets CSRF token in cookie
        self.client.get(reverse('session'), {'sess': self.app_sess_login.code})

        # test application session login
        self.client.handler.enforce_csrf_checks = True
        valid_data = {'sess': self.app_sess_login.code, 'username': self.user.username, 'password': self.user_password}
        url = reverse('session_login')

        # failures
        self.assertEqual(self.client.get(url, data=valid_data).status_code, status.HTTP_400_BAD_REQUEST)  # wrong method
        self.assertEqual(self.client.post_json(url, data={}).status_code, status.HTTP_400_BAD_REQUEST)  # no data
        # wrong application session
        self.assertEqual(self.client.post_json(url, data={
            'sess': 'foo', 'username': self.user.username, 'password': self.user_password
        }).status_code, status.HTTP_404_NOT_FOUND)
        # application session that doesn't require login
        self.assertEqual(self.client.post_json(url, data={
            'sess': self.app_sess_no_auth.code, 'username': self.user.username, 'password': self.user_password
        }).status_code, status.HTTP_400_BAD_REQUEST)
        # wrong user name
        self.assertEqual(self.client.post_json(url, data={
            'sess': self.app_sess_login.code, 'username': 'foo', 'password': self.user_password
        }).status_code, status.HTTP_404_NOT_FOUND)
        # wrong password
        self.assertEqual(self.client.post_json(url, data={
            'sess': self.app_sess_login.code, 'username': self.user.username, 'password': 'foo'
        }).status_code, status.HTTP_401_UNAUTHORIZED)
        # no CSRF token
        self.assertEqual(self.client.post_json(url, data=valid_data, omit_csrftoken=True).status_code,
                         status.HTTP_401_UNAUTHORIZED)

        # OK
        response = self.client.post_json(url, data=valid_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user_app_sess = UserApplicationSession.objects.get(application_session=self.app_sess_login)
        self.assertEqual(user_app_sess.user.id, self.user.id)
        self.assertIsInstance(user_app_sess.code, str)
        self.assertTrue(len(user_app_sess.code) == 64)
        self.assertEqual(response.json(), {
            'sess_code': valid_data['sess'],
            'auth_mode': 'login',
            'user_code': user_app_sess.code,
            'config': self.app_sess_no_auth.config.config
        })

    def test_start_tracking(self):
        # request application session – also sets CSRF token in cookie
        response = self.client.get(reverse('session'), {'sess': self.app_sess_no_auth.code})
        auth_token = response.json()['user_code']

        # test start tracking
        url = reverse('start_tracking')
        now = datetime.utcnow()
        valid_data = {'sess': self.app_sess_no_auth.code, 'start_time': now.isoformat()}

        # failures
        # TODO

        # OK without device_info
        response = self.client.post_json(url, data=valid_data, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        tracking_sess = TrackingSession.objects.get(user_app_session__code=auth_token)
        self.assertEqual(response.json(), {'tracking_session_id': tracking_sess.id})
        self.assertEqual(tracking_sess.start_time, now.astimezone(timezone.utc))

        # OK with device_info
        # TODO
