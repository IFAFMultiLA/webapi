from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import ApplicationSession, ApplicationConfig, UserApplicationSession, User


@ensure_csrf_cookie
def app_session(request):
    """
    Request an application session identified via GET parameter `sess`. If an application session requires user
    authentication, this view will only return the `auth_mode` as `"login"` along with an CSRF cookie. In this case,
    the client must proceed with the `app_session_login` view. If an application session does not require user
    authentication, this view will additionally generate a user code `user_code` (that must be used as authentication
    token for further requests) and an application configuration.

    Checking this view locally with `curl`:

        curl -c /tmp/cookies.txt -b /tmp/cookies.txt \
            -i http://127.0.0.1:8000/session/?sess=<SESS_CODE>
    """

    if request.method == 'GET':
        sess_code = request.GET.get('sess', None)
        if sess_code:
            # get the application session
            app_sess_obj = get_object_or_404(ApplicationSession, code=sess_code)
            response_data = {'sess_code': app_sess_obj.code, 'auth_mode': app_sess_obj.auth_mode}

            if app_sess_obj.auth_mode == 'none':
                # create a user code
                app_config_obj, user_sess_obj = _generate_user_session(app_sess_obj)    # user_id will stay None

                # set additional response data
                response_data.update({
                    'user_code': user_sess_obj.code,
                    'config': app_config_obj.config
                })

                status = 201
            else:
                # user must login; no additional response data
                status = 200

            return JsonResponse(response_data, status=status)

    return HttpResponse(status=400)


def app_session_login(request):
    """
    Log in for an application session that requires authentication. The following data must be provided via POST:

    - application session code as `sess`
    - user name as `username`
    - user password as `password`

    Will return a generated `user_code` (that must be used as authentication token for further requests) and an
    application configuration. Requires a CSRF token.

    Checking this view locally with `curl`:

    1. Make a GET request to the /session/ endpoint as shown in the `app_session` view documentation. Note the CSRF
       token.

    2. Then run the following:

        curl -d "sess=<SESS_CODE>&username=<USER>&password=<PASSWORD>" \
            -c /tmp/cookies.txt -b /tmp/cookies.txt \
            -H "X-CSRFToken: <CSRFTOKEN>" \
            -i http://127.0.0.1:8000/session_login/
    """

    if request.method == 'POST':
        sess_code = request.POST.get('sess', None)
        username = request.POST.get('username', None)
        password = request.POST.get('password', None)

        if sess_code and username and password:
            app_sess_obj = get_object_or_404(ApplicationSession, code=sess_code)
            user_obj = get_object_or_404(User, username=username)

            if user_obj.check_password(password):
                app_config_obj, user_sess_obj = _generate_user_session(app_sess_obj, user_obj)

                return JsonResponse({
                    'sess_code': app_sess_obj.code,
                    'auth_mode': app_sess_obj.auth_mode,
                    'user_code': user_sess_obj.code,
                    'config': app_config_obj.config
                }, status=201)

    return HttpResponse(status=400)


def csrf_failure(request, reason=""):
    """
    Default CSRF failure view to format a CSRF failure as JSON.
    """
    return JsonResponse({'error': 'CSRF check failure.', 'reason': reason}, status=401)


def _generate_user_session(app_sess_obj, user_obj=None):
    """
    Create a new user session for an application session `app_sess_obj`. For an anonymous session, no `user_obj` must be
    provided, otherwise pass a `User` instance as `user_obj`. In both cases will generate a user session code to be used
    as authentication token.
    """
    app_config_obj = get_object_or_404(ApplicationConfig, id=app_sess_obj.config_id)

    user_sess_obj = UserApplicationSession(application_session=app_sess_obj, user=user_obj)
    user_sess_obj.generate_code()
    user_sess_obj.save(force_insert=True)

    return app_config_obj, user_sess_obj
