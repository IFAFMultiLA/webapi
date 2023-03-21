from functools import wraps

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.parsers import JSONParser

from .models import ApplicationSession, ApplicationConfig, UserApplicationSession, User, TrackingSession
from .serializers import TrackingSessionSerializer


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


def require_user_session_token(view_fn):
    @wraps(view_fn)
    def wrap(request, *args, **kwargs):
        auth_data = request.headers.get('authorization').split(' ')
        if len(auth_data) == 2 and auth_data[0].lower() == 'token':
            token = auth_data[1]
            data = JSONParser().parse(request)
            sess = data.get('sess', None)

            if token and sess:
                try:
                    user_app_sess_obj = UserApplicationSession.objects.get(application_session_id=sess, code=token)
                except UserApplicationSession.DoesNotExist:
                    return HttpResponse(status=404)

                if user_app_sess_obj.application_session.auth_mode == 'login' and not user_app_sess_obj.user:
                    # login requires a user object
                    return HttpResponse(status=404)

                request.user = user_app_sess_obj.user
                return view_fn(request, *args, user_app_sess_obj=user_app_sess_obj, parsed_data=data, **kwargs)

        return HttpResponse(status=404)

    return wrap


def require_tracking_session(view_fn):
    @wraps(view_fn)
    def wrap(request, user_app_sess_obj, parsed_data):
        tracking_session_id = parsed_data.get('tracking_session_id', None)

        if tracking_session_id:
            try:
                tracking_sess_obj = TrackingSession.objects.get(id=tracking_session_id,
                                                                user_app_session_id=user_app_sess_obj.pk)
            except TrackingSession.DoesNotExist:
                return HttpResponse(status=400)

            return view_fn(request,
                           user_app_sess_obj=user_app_sess_obj,
                           parsed_data=parsed_data,
                           tracking_sess_obj=tracking_sess_obj)

        return HttpResponse(status=404)

    return wrap


@require_user_session_token
def start_tracking(request, user_app_sess_obj, parsed_data):
    if request.method == 'POST':
        parsed_data['user_app_session'] = user_app_sess_obj.pk
        tracking_sess_serializer = TrackingSessionSerializer(data=parsed_data)
        if tracking_sess_serializer.is_valid():
            tracking_sess_serializer.save()
            return JsonResponse({'tracking_session_id': tracking_sess_serializer.instance.pk})

    return HttpResponse(status=400)


@require_user_session_token
@require_tracking_session
def stop_tracking(request, user_app_sess_obj, parsed_data, tracking_sess_obj):
    if request.method == 'POST' and not tracking_sess_obj.end_time:
        tracking_sess_serializer = TrackingSessionSerializer(tracking_sess_obj,
                                                             data={'end_time': parsed_data['end_time']},
                                                             partial=True)
        if tracking_sess_serializer.is_valid():
            tracking_sess_serializer.save()
            return JsonResponse({'tracking_session_id': tracking_sess_serializer.instance.pk})

    return HttpResponse(status=400)


@require_user_session_token
@require_tracking_session
def track_event(request, user_app_sess_obj, parsed_data, tracking_sess_obj):
    if request.method == 'POST':
        pass

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
