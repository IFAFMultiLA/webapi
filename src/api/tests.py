"""
Automated tests.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.conf import settings
from django.forms.models import ModelFormMetaclass, ModelForm
from django.http import SimpleCookie, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.db.utils import IntegrityError
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from .models import max_options_length, current_time_bytes, Application, ApplicationConfig, ApplicationSession, \
    UserApplicationSession, User, TrackingSession, TrackingEvent, UserFeedback, ApplicationSessionGate
from .serializers import TrackingSessionSerializer, TrackingEventSerializer, UserFeedbackSerializer
from .admin import admin_site, ApplicationAdmin, ApplicationConfigAdmin, ApplicationSessionAdmin, \
    TrackingSessionAdmin, TrackingEventAdmin, UserFeedbackAdmin, ApplicationSessionGateAdmin


# ----- helper functions -----


def tznow():
    return datetime.now(ZoneInfo(settings.TIME_ZONE))


# ----- models -----


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


class UserFeedbackModelTests(TestCase):
    """
    Test case for UserFeedback model.
    """

    def setUp(self):
        # create an app for an app config
        app = Application.objects.create(name='test app', url='https://test.app')
        # create an app config for an app session
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})
        # create an app session
        app_sess = ApplicationSession(config=app_config, auth_mode='none')
        app_sess.generate_code()
        app_sess.save()
        # create an user app session
        self.user_app_sess = UserApplicationSession(application_session=app_sess)
        self.user_app_sess.generate_code()
        self.user_app_sess.save()
        # create a tracking session
        self.tracking_sess = TrackingSession.objects.create(user_app_session=self.user_app_sess,
                                                            start_time=tznow())

    def test_user_feedback_linked_to_user_app_sess(self):
        feedback = UserFeedback.objects.create(user_app_session=self.user_app_sess, content_section='#foo', text='bar')
        self.assertIs(feedback.user_app_session, self.user_app_sess)
        self.assertIs(feedback.tracking_session, None)
        self.assertEqual(feedback.content_section, '#foo')
        self.assertIsNone(feedback.score)
        self.assertEqual(feedback.text, 'bar')
        self.assertIsInstance(feedback.created, datetime)

    def test_user_feedback_linked_to_tracking_sess(self):
        feedback = UserFeedback.objects.create(user_app_session=self.user_app_sess, tracking_session=self.tracking_sess,
                                               content_section='#foo', score=1)
        self.assertIs(feedback.user_app_session, self.user_app_sess)
        self.assertIs(feedback.tracking_session, self.tracking_sess)
        self.assertEqual(feedback.content_section, '#foo')
        self.assertEqual(feedback.score, 1)
        self.assertIsNone(feedback.text)
        self.assertIsInstance(feedback.created, datetime)

    def test_user_feedback_all_fields_set(self):
        feedback = UserFeedback.objects.create(user_app_session=self.user_app_sess,
                                               tracking_session=self.tracking_sess,
                                               content_section='#foo',
                                               score=2,
                                               text='bar')
        self.assertIs(feedback.user_app_session, self.user_app_sess)
        self.assertIs(feedback.tracking_session, self.tracking_sess)
        self.assertEqual(feedback.content_section, '#foo')
        self.assertEqual(feedback.score, 2)
        self.assertEqual(feedback.text, 'bar')
        self.assertIsInstance(feedback.created, datetime)

    def test_user_feedback_unique_constraint(self):
        args = dict(user_app_session=self.user_app_sess, content_section='#uniquetest1', score=2)
        UserFeedback.objects.create(**args)
        UserFeedback.objects.create(user_app_session=self.user_app_sess, content_section='#uniquetest2', score=3)

        with self.assertRaisesRegex(IntegrityError, r'unique constraint(?i)'):
            UserFeedback.objects.create(**args)

    def test_user_feedback_either_score_or_text_must_be_given_constraint(self):
        args = dict(user_app_session=self.user_app_sess, content_section='#uniquetest1')
        with self.assertRaisesRegex(IntegrityError, r'either_score_or_text_must_be_given'):
            UserFeedback.objects.create(**args)

    def test_user_feedback_score_validators(self):
        class UserFeedbackForm(ModelForm):
            class Meta:
                model = UserFeedback
                fields = ['user_app_session', 'content_section', 'score']

        data = dict(user_app_session=self.user_app_sess.pk,
                    content_section='#foo',
                    score=-1)
        form = UserFeedbackForm(data)
        self.assertFalse(form.is_valid())

        data['score'] = 6
        form = UserFeedbackForm(data)
        self.assertFalse(form.is_valid())


# ----- serializers -----


class TrackingSessionSerializerTests(TestCase):
    """
    Test case for tracking session serializer.
    """

    def setUp(self):
        # create an app for an app config
        app = Application.objects.create(name='test app', url='https://test.app')
        # create an app config for an app session
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})
        # create an app session
        app_sess = ApplicationSession(config=app_config, auth_mode='none')
        app_sess.generate_code()
        app_sess.save()
        # create an user app session
        self.user_app_sess = UserApplicationSession(application_session=app_sess)
        self.user_app_sess.generate_code()
        self.user_app_sess.save()

    def test_serializer(self):
        for whichtime in ('start_time', 'end_time'):
            for tdelta, expect_valid in ((dict(minutes=-6), False),
                                         (dict(minutes=-3), True),
                                         (dict(minutes=0), True),
                                         (dict(seconds=1), True),
                                         (dict(seconds=10), False)):
                data = dict(user_app_session=self.user_app_sess.pk, device_info='foo')
                data['start_time'] = tznow()
                data[whichtime] = tznow() + timedelta(**tdelta)
                ser = TrackingSessionSerializer(data=data)
                valid = ser.is_valid()
                self.assertEqual(valid, expect_valid)


class TrackingEventSerializerTests(TestCase):
    """
    Test case for tracking event serializer.
    """

    def setUp(self):
        # create an app for an app config
        app = Application.objects.create(name='test app', url='https://test.app')
        # create an app config for an app session
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})
        # create an app session
        app_sess = ApplicationSession(config=app_config, auth_mode='none')
        app_sess.generate_code()
        app_sess.save()
        # create an user app session
        user_app_sess = UserApplicationSession(application_session=app_sess)
        user_app_sess.generate_code()
        user_app_sess.save()
        # create a tracking session
        self.tracking_sess = TrackingSession.objects.create(user_app_session=user_app_sess,
                                                            start_time=tznow())

    def test_serializer(self):
        for tdelta, expect_valid in ((dict(minutes=-6), False),
                                     (dict(minutes=-3), True),
                                     (dict(minutes=0), True),
                                     (dict(seconds=1), True),
                                     (dict(seconds=10), False)):
            data = dict(tracking_session=self.tracking_sess.pk,
                        time=tznow() + timedelta(**tdelta),
                        type='foo',
                        value='bar')
            ser = TrackingEventSerializer(data=data)
            valid = ser.is_valid()
            self.assertEqual(valid, expect_valid)


class UserFeedbackSerializerTests(TestCase):
    """
    Test case for user feedback serializer.
    """

    def setUp(self):
        # create an app for an app config
        app = Application.objects.create(name='test app', url='https://test.app')
        # create an app config for an app session
        app_config = ApplicationConfig.objects.create(application=app, label='test config', config={'test': True})
        # create an app session
        app_sess = ApplicationSession(config=app_config, auth_mode='none')
        app_sess.generate_code()
        app_sess.save()
        # create an user app session
        self.user_app_sess = UserApplicationSession(application_session=app_sess)
        self.user_app_sess.generate_code()
        self.user_app_sess.save()
        # create a tracking session
        self.tracking_sess = TrackingSession.objects.create(user_app_session=self.user_app_sess,
                                                            start_time=tznow())

    def test_serializer(self):
        for tracking_sess in (None, self.tracking_sess):
            for score in range(-1, 6):
                data = dict(user_app_session=self.user_app_sess.pk,
                            tracking_session=tracking_sess.pk if tracking_sess else None,
                            content_section='#foo',
                            score=score,
                            text='bar')
                ser = UserFeedbackSerializer(data=data)
                valid = ser.is_valid()
                if 1 <= score <= 5:
                    self.assertTrue(valid)
                else:
                    self.assertFalse(valid)


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
        app_config_no_feedback = ApplicationConfig.objects.create(application=app,
                                                                  label='config. w/ no qualitative feedback',
                                                                  config={'feedback': False})

        # create an app session w/o authentication
        self.app_sess_no_auth = ApplicationSession(config=app_config, auth_mode='none')
        self.app_sess_no_auth.generate_code()
        self.app_sess_no_auth.save()

        # create an app session that requires login
        self.app_sess_login = ApplicationSession(config=app_config, auth_mode='login')
        self.app_sess_login.generate_code()
        self.app_sess_login.save()

        # create an app session using the "no qual. feedback" config
        self.app_sess_no_auth_no_feedback = ApplicationSession(config=app_config_no_feedback,
                                                               auth_mode='none')
        self.app_sess_no_auth_no_feedback.generate_code()
        self.app_sess_no_auth_no_feedback.save()

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

    def test_index(self):
        response = self.client.get('')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.headers['Content-Type'].startswith('text/html'))

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
            'device_info': {'test key': 'test value', 'client_ip': '127.0.0.1'}
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

    def test_user_feedback(self):
        # request application session – also sets CSRF token in cookie
        response = self.client.get(reverse('session'), {'sess': self.app_sess_no_auth.code})
        auth_token = response.json()['user_code']

        # start tracking
        response = self.client.post_json(reverse('start_tracking'),
                                         data={'sess': self.app_sess_no_auth.code,
                                               'start_time': tznow().isoformat()},
                                         auth_token=auth_token)

        tracking_sess_id = response.json()['tracking_session_id']

        # --- test user feedback POST method ---
        url = reverse('user_feedback')
        base_data = {'sess': self.app_sess_no_auth.code, 'content_section': '#foo'}
        base_data_other_section = {'sess': self.app_sess_no_auth.code, 'content_section': '#foo2'}
        base_data_other_section2 = {'sess': self.app_sess_no_auth.code, 'content_section': '#foo999'}
        valid_data_no_track = dict(**base_data, score=3, text="bar")
        valid_data_no_track_upd = dict(**base_data, score=2, text="")
        valid_data_no_track2 = dict(**base_data_other_section2, tracking_session=None, score=1, text="foobar")
        valid_data_with_track = dict(**base_data_other_section, tracking_session=tracking_sess_id, score=3, text="bar")

        # failures
        self.assertEqual(self.client.post_json(url, data={}, auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # no data
        self.assertEqual(self.client.post_json(url, data={'sess': 'foo'}, auth_token=auth_token).status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong application session
        self.assertEqual(self.client.post_json(url, data=base_data,
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # missing score or text
        self.assertEqual(self.client.post_json(url, data={'sess': self.app_sess_no_auth.code, 'score': 3},
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # missing content_section
        self.assertEqual(self.client.post_json(url, data=dict(**valid_data_no_track, tracking_session=0),
                                               auth_token=auth_token).status_code,
                         status.HTTP_400_BAD_REQUEST)  # wrong tracking_session
        self.assertEqual(self.client.post_json(url, data=valid_data_no_track, auth_token='foo').status_code,
                         status.HTTP_401_UNAUTHORIZED)  # wrong auth token
        # invalid scores
        for score in {-1, 0, 6, 1000, "x"}:
            response = self.client.post_json(url, data=dict(**base_data, score=score), auth_token=auth_token)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertTrue('validation_errors' in response.json())

        # check that the app config is obeyed
        # request application session – also sets CSRF token in cookie
        auth_token_no_feedback = self.client.get(
            reverse('session'), {'sess': self.app_sess_no_auth_no_feedback.code}
        ).json()['user_code']
        base_data_no_feedback = {'sess': self.app_sess_no_auth_no_feedback.code, 'content_section': '#foo'}
        valid_data_no_feedback = dict(**base_data_no_feedback, score=3, text="bar")
        response = self.client.post_json(url, data=valid_data_no_feedback, auth_token=auth_token_no_feedback)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # feedback not allowed

        # reset
        UserFeedback.objects.all().delete()

        # OK without tracking session
        response = self.client.post_json(url, data=valid_data_no_track, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.content), 0)
        self.assertEqual(UserFeedback.objects.count(), 1)
        user_feedback_obj = UserFeedback.objects.latest('created')
        self.assertEqual(user_feedback_obj.user_app_session.code, auth_token)
        self.assertIsNone(user_feedback_obj.tracking_session)
        self.assertEqual(user_feedback_obj.content_section, valid_data_no_track['content_section'])
        self.assertEqual(user_feedback_obj.score, valid_data_no_track['score'])
        self.assertEqual(user_feedback_obj.text, valid_data_no_track['text'])

        # update existing user feedback
        response = self.client.post_json(url, data=valid_data_no_track_upd, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.content), 0)
        self.assertEqual(UserFeedback.objects.count(), 1)
        user_feedback_obj_updated = UserFeedback.objects.get(user_app_session__code=auth_token,
                                                             content_section=valid_data_no_track_upd['content_section'])
        self.assertEqual(user_feedback_obj_updated.pk, user_feedback_obj.pk)
        self.assertIsNone(user_feedback_obj_updated.tracking_session)
        self.assertEqual(user_feedback_obj_updated.score, valid_data_no_track_upd['score'])
        self.assertEqual(user_feedback_obj_updated.text, valid_data_no_track_upd['text'])


        # OK without tracking session
        response = self.client.post_json(url, data=valid_data_no_track2, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.content), 0)
        self.assertEqual(UserFeedback.objects.count(), 2)
        user_feedback_obj = UserFeedback.objects.latest('created')
        self.assertEqual(user_feedback_obj.user_app_session.code, auth_token)
        self.assertIsNone(user_feedback_obj.tracking_session)
        self.assertEqual(user_feedback_obj.content_section, valid_data_no_track2['content_section'])
        self.assertEqual(user_feedback_obj.score, valid_data_no_track2['score'])
        self.assertEqual(user_feedback_obj.text, valid_data_no_track2['text'])

        # OK with tracking session
        response = self.client.post_json(url, data=valid_data_with_track, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.content), 0)
        self.assertEqual(UserFeedback.objects.count(), 3)
        user_feedback_obj = UserFeedback.objects.latest('created')
        self.assertEqual(user_feedback_obj.user_app_session.code, auth_token)
        self.assertEqual(user_feedback_obj.tracking_session.pk, valid_data_with_track['tracking_session'])
        self.assertEqual(user_feedback_obj.content_section, valid_data_with_track['content_section'])
        self.assertEqual(user_feedback_obj.score, valid_data_with_track['score'])
        self.assertEqual(user_feedback_obj.text, valid_data_with_track['text'])

        # OK without tracking session and only score but no text
        valid_data_no_track_no_text = dict(sess=self.app_sess_no_auth.code, content_section='#foo3', score=5)
        response = self.client.post_json(url, data=valid_data_no_track_no_text, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.content), 0)
        self.assertEqual(UserFeedback.objects.count(), 4)
        user_feedback_obj = UserFeedback.objects.latest('created')
        self.assertEqual(user_feedback_obj.user_app_session.code, auth_token)
        self.assertIsNone(user_feedback_obj.tracking_session)
        self.assertEqual(user_feedback_obj.content_section, valid_data_no_track_no_text['content_section'])
        self.assertEqual(user_feedback_obj.score, valid_data_no_track_no_text['score'])
        self.assertIsNone(user_feedback_obj.text)

        # OK without tracking session and only score but no text
        valid_data_no_track_no_score = dict(sess=self.app_sess_no_auth.code, content_section='#foo4', text="some text")
        response = self.client.post_json(url, data=valid_data_no_track_no_score, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.content), 0)
        self.assertEqual(UserFeedback.objects.count(), 5)
        user_feedback_obj = UserFeedback.objects.latest('created')
        self.assertEqual(user_feedback_obj.user_app_session.code, auth_token)
        self.assertIsNone(user_feedback_obj.tracking_session)
        self.assertEqual(user_feedback_obj.content_section, valid_data_no_track_no_score['content_section'])
        self.assertIsNone(user_feedback_obj.score)
        self.assertEqual(user_feedback_obj.text, valid_data_no_track_no_score['text'])

        # --- test user feedback GET method ---
        valid_data = {'sess': self.app_sess_no_auth.code}

        # missing auth token
        self.assertEqual(self.client.get(url).status_code, status.HTTP_401_UNAUTHORIZED)
        # wrong auth token
        self.assertEqual(self.client.get(url, valid_data, auth_token='foo').status_code,
                         status.HTTP_401_UNAUTHORIZED)
        # missing data
        self.assertEqual(self.client.get(url, auth_token=auth_token).status_code, status.HTTP_400_BAD_REQUEST)
        # wrong sess ID
        self.assertEqual(self.client.get(url, {'sess': 'foo'}, auth_token=auth_token).status_code,
                         status.HTTP_401_UNAUTHORIZED)

        # OK
        response = self.client.get(url, valid_data, auth_token=auth_token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        respdata = response.json()
        self.assertEqual(set(respdata.keys()), {'user_feedback'})
        self.assertIsInstance(respdata['user_feedback'], list)
        self.assertEqual(len(respdata['user_feedback']), 5)

        for fbdata in respdata['user_feedback']:
            self.assertEqual(set(fbdata.keys()), {'content_section', 'score', 'text'})
            self.assertIsInstance(fbdata['content_section'], str)
            self.assertTrue(len(fbdata['content_section']) > 0)
            if fbdata['score'] is not None:
                self.assertIsInstance(fbdata['score'], int)
                self.assertTrue(1 <= fbdata['score'] <= 5)
            if fbdata['text'] is not None:
                self.assertIsInstance(fbdata['text'], str)

    def test_app_session_gate(self):
        # fail: non existent gate session code
        self.assertEqual(self.client.get(reverse('gate', args=['nonexistent'])).status_code, status.HTTP_404_NOT_FOUND)

        # prepare data: generate app, app config and three app sessions
        app = Application(name="testapp", url="http://testapp.com", updated_by=self.user)
        app.save()
        appconfig = ApplicationConfig(application=app,
                                      label="testconfig",
                                      config={"key": "value"},
                                      updated_by=self.user)
        appconfig.save()

        app_sessions = []
        for _ in range(3):
            appsess = ApplicationSession(config=appconfig, auth_mode='none')
            appsess.generate_code()
            appsess.save()
            app_sessions.append(appsess)
        # it's important to sort them, as this determines the order in which the redirects happen
        app_sessions = sorted(app_sessions, key=lambda x: x.code)
        app_session_codes = {sess.code for sess in app_sessions}

        # iterate through the number of app sessions that we want to assign to a gate
        for n_app_sessions in range(len(app_sessions)):
            # create the gate with `n_app_sessions` app sessions
            gate = ApplicationSessionGate(label=f"testgate {n_app_sessions}")
            gate.generate_code()
            gate.save()
            gate.app_sessions.set(app_sessions[:n_app_sessions])

            # visit the gate with that session multiple times
            for reset_cookies in (False, True):
                for i in range(n_app_sessions*2):
                    response = self.client.get(reverse('gate', args=[gate.code]))
                    if n_app_sessions == 0:
                        # gate with no assigned app sessions always returns "204 no content"
                        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
                    else:
                        # gates with assigned app sessions should answer with a redirect to the respective app session
                        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
                        self.assertIn('gate_app_sess_' + gate.code, response.cookies)
                        self.assertIn(response.cookies['gate_app_sess_' + gate.code].value, app_session_codes)
                        if reset_cookies:
                            # cookies were reset – simulates a new client -> visit one app session after another,
                            # e.g. A -> B -> A -> B -> ... for a gate with 2 app sessions
                            appsess = app_sessions[i % n_app_sessions]
                            self.assertEqual(response.url, appsess.session_url())
                        else:
                            # cookies remain – simulates same client -> visit same app session as before (here, always
                            # the first of the gate)
                            self.assertEqual(response.url, app_sessions[0].session_url())

                    if reset_cookies:
                        self.client.cookies = SimpleCookie()


# ----- admin -----


class ModelAdminTests(TestCase):
    def _check_modeladmin_default_views(self, modeladm, view_args=None, check_views=None, custom_request=None,
                                        changelist_redirect=None):
        view_args = view_args or {}
        check_views = check_views or ('add_view', 'change_view', 'changelist_view', 'delete_view', 'history_view')
        for viewfn_name in check_views:
            viewfn = getattr(modeladm, viewfn_name)
            args = view_args.get(viewfn_name, {})
            response = viewfn(custom_request or self.request, **args)
            if viewfn_name == 'changelist_view' and changelist_redirect:
                self.assertIsInstance(response, HttpResponseRedirect)
                self.assertEqual(response.url, changelist_redirect)
                self.assertEqual(response.status_code, 302)
            else:
                self.assertIsInstance(response, TemplateResponse)
                self.assertEqual(response.status_code, 200)

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username="admin", email="admin@example.com", password="test")
        cls.request = RequestFactory().get('/admin')
        cls.request.user = cls.user

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
        request = RequestFactory().get('/admin', {'application': app.pk})
        request.session = {}
        request.user = self.user
        del app

        # check that "updated" and "updated_by" are correctly set
        form = modeladm.get_form(request, change=False)
        modeladm.save_model(request, appconfig, form, change=False)
        self.assertEqual(appconfig.updated_by, request.user)
        self.assertIsNotNone(appconfig.updated)

        form = modeladm.get_form(request, appconfig, change=True)
        modeladm.save_model(request, appconfig, form, change=True)
        self.assertEqual(appconfig.updated_by, request.user)
        self.assertIsNotNone(appconfig.updated)

        # the instance in the DB is the same
        appconfig_from_db = ApplicationConfig.objects.get(label="testconfig")
        self.assertEqual(appconfig_from_db.pk, appconfig.pk)

        obj_views_args = dict(object_id=str(appconfig.pk))
        views_args = {'change_view': obj_views_args, 'delete_view': obj_views_args, 'history_view': obj_views_args}
        self._check_modeladmin_default_views(modeladm, views_args, custom_request=request,
                                             changelist_redirect=reverse('admin:api_application_changelist'))

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
        request = RequestFactory().get('/admin', {'config': appconfig.pk})
        request.session = {}
        request.user = self.user
        del app

        # check that a session code is generated
        form = modeladm.get_form(request, change=False)
        modeladm.save_model(request, appsess, form, change=False)
        self.assertIsInstance(appsess.code, str)
        self.assertEqual(len(appsess.code), 10)
        self.assertEqual(appsess.updated_by, request.user)
        self.assertIsNotNone(appsess.updated)

        # check that "updated" and "updated_by" are correctly set on update
        form = modeladm.get_form(request, appsess, change=True)
        modeladm.save_model(request, appsess, form, change=True)
        self.assertEqual(appsess.updated_by, request.user)
        self.assertIsNotNone(appsess.updated)

        appsess_from_db = ApplicationSession.objects.get(config=appconfig)
        self.assertEqual(appsess_from_db.pk, appsess.pk)

        obj_views_args = dict(object_id=str(appsess.pk))
        views_args = {'change_view': obj_views_args, 'delete_view': obj_views_args, 'history_view': obj_views_args}
        self._check_modeladmin_default_views(modeladm, views_args, custom_request=request,
                                             changelist_redirect=reverse('admin:api_application_changelist'))

    def test_applicationsessiongate_admin(self):
        modeladm = ApplicationSessionGateAdmin(ApplicationSessionGate, admin_site)

        app = Application(name="testapp", url="http://testapp.com", updated_by=self.request.user)
        app.save()
        appconfig = ApplicationConfig(application=app,
                                      label="testconfig",
                                      config={"key": "value"},
                                      updated_by=self.request.user)
        appconfig.save()
        appsess1 = ApplicationSession(config=appconfig, auth_mode='none')
        appsess1.generate_code()
        appsess1.save()
        appsess2 = ApplicationSession(config=appconfig, auth_mode='none')
        appsess2.generate_code()
        appsess2.save()

        gate = ApplicationSessionGate(label="testgate")
        del app

        # check that a session code is generated
        form = modeladm.get_form(self.request, change=False)
        modeladm.save_model(self.request, gate, form, change=False)
        gate.app_sessions.set([appsess1, appsess2])
        self.assertIsInstance(gate.code, str)
        self.assertEqual(len(gate.code), 10)
        self.assertEqual(gate.updated_by, self.request.user)
        self.assertIsNotNone(gate.updated)

        # check that "updated" and "updated_by" are correctly set on update
        form = modeladm.get_form(self.request, gate, change=True)
        modeladm.save_model(self.request, gate, form, change=True)
        self.assertEqual(gate.updated_by, self.request.user)
        self.assertIsNotNone(gate.updated)

        gate_from_db = ApplicationSessionGate.objects.latest('updated')
        self.assertEqual(gate_from_db.pk, gate.pk)

        obj_views_args = dict(object_id=str(gate.pk))
        views_args = {'change_view': obj_views_args, 'delete_view': obj_views_args, 'history_view': obj_views_args}
        self._check_modeladmin_default_views(modeladm, views_args)

    def test_userfeedback_admin(self):
        modeladm = UserFeedbackAdmin(UserFeedback, admin_site)

        app = Application(name="testapp", url="http://testapp.com", updated_by=self.request.user)
        app.save()
        appconfig = ApplicationConfig(application=app,
                                      label="testconfig",
                                      config={"key": "value"},
                                      updated_by=self.request.user)
        appconfig.save()
        appsess = ApplicationSession(config=appconfig, auth_mode='none')
        appsess.generate_code()
        appsess.save()

        for filter_by, filter_obj in (
                ('application', app),
                ('applicationconfig', appconfig),
                ('applicationsession', appsess),
        ):
            custom_request = RequestFactory().get('/admin', data={filter_by + '_id': filter_obj.pk})
            custom_request.user = self.request.user

            self._check_modeladmin_default_views(modeladm,
                                                 check_views=['changelist_view'],
                                                 custom_request=custom_request)

    def test_trackingsession_admin(self):
        modeladm = TrackingSessionAdmin(TrackingSession, admin_site)

        self._check_modeladmin_default_views(modeladm, check_views=['changelist_view'])

    def test_trackingevent_admin(self):
        modeladm = TrackingEventAdmin(TrackingEvent, admin_site)

        self._check_modeladmin_default_views(modeladm, check_views=['changelist_view'])
