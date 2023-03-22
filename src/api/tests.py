from datetime import datetime, timedelta

from django.test import TestCase

from .models import max_options_length, current_time_bytes, Application, ApplicationConfig, ApplicationSession, \
    UserApplicationSession


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
