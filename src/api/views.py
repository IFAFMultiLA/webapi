"""
Views that expose the API endpoints.

API endpoints that accept data via POST must pass the data in JSON format. When dates or times are passed, they must
be formatted in ISO 8601 format.

Markus Konrad <markus.konrad@htw-berlin.de>, March 2023.
"""

from functools import wraps

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.parsers import JSONParser
from rest_framework.exceptions import ParseError

from .models import ApplicationSession, ApplicationConfig, UserApplicationSession, User, TrackingSession
from .serializers import TrackingSessionSerializer, TrackingEventSerializer


# --- decorators ---


def require_user_session_token(view_fn):
    """
    Decorator to ensure that a user session token is passed in the request and checked before the view `view_fn` is
    called. Additionally, an application session code must be passed via JSON as `sess`.

    The user session token must be passed in the request header as 'Authorization: Token <token code>'. If anything
    fails, an HTTP 404 response is returned, otherwise the view `view_fn` is called and its response is returned. Will
    pass the user application session object `user_app_sess_obj` and the parsed JSON data as `parsed_data` to the
    view function.
    """
    @wraps(view_fn)
    def wrap(request, *args, **kwargs):
        # get authorization header
        auth_data = request.headers.get('authorization', '').split(' ')  # expected format: "Token <token code>"
        if len(auth_data) == 2 and auth_data[0].lower() == 'token':
            # get the token
            token = auth_data[1]

            # parse the passed JSON data
            try:
                data = JSONParser().parse(request)
            except ParseError as exc:
                return HttpResponse(f'JSON parsing error: {str(exc).encode("utf-8")}', status=400)

            # get the application session code
            sess = data.get('sess', None)

            if token and sess:
                try:
                    # get the user application session for the given application session and user session token
                    user_app_sess_obj = UserApplicationSession.objects.get(application_session_id=sess, code=token)
                except UserApplicationSession.DoesNotExist:
                    return HttpResponse(status=401)

                if user_app_sess_obj.application_session.auth_mode == 'login' and not user_app_sess_obj.user:
                    # login requires a user object
                    return HttpResponse(status=401)
                elif user_app_sess_obj.application_session.auth_mode == 'none' and user_app_sess_obj.user:
                    # when auth mode is "none", the user should be anonymous
                    raise RuntimeError('application session authentication mode is "none" but user is authenticated '
                                       f'as "{user_app_sess_obj.user}"')

                # set the authenticated user (will be None if auth_mode is "none")
                request.user = user_app_sess_obj.user
                return view_fn(request, *args, user_app_sess_obj=user_app_sess_obj, parsed_data=data, **kwargs)
            else:
                return HttpResponse(status=400)
        return HttpResponse(status=401)

    return wrap


def require_tracking_session(view_fn):
    """
    Decorator to ensure that the request contains a valid tracking session ID. Will obtain that tracking session and
    pass it to the view function `view_fn` along with the user application session object `user_app_sess_obj` and the
    parsed JSON data `parsed_data`.
    """
    @wraps(view_fn)
    def wrap(request, user_app_sess_obj, parsed_data):
        # get tracking session ID
        tracking_session_id = parsed_data.get('tracking_session_id', None)

        if tracking_session_id:
            try:
                # get tracking session for this user application session
                tracking_sess_obj = TrackingSession.objects.get(id=tracking_session_id,
                                                                user_app_session_id=user_app_sess_obj.pk,
                                                                end_time__isnull=True)  # tracking session still active
            except TrackingSession.DoesNotExist:
                return HttpResponse(status=400)

            return view_fn(request,
                           user_app_sess_obj=user_app_sess_obj,
                           parsed_data=parsed_data,
                           tracking_sess_obj=tracking_sess_obj)

        return HttpResponse(status=400)

    return wrap


# --- views ---


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

        curl -d '{"sess": "<SESS_CODE>", "username": "<USER>", "password": "<PASSWORD>"}' \
            -c /tmp/cookies.txt -b /tmp/cookies.txt \
            -H "X-CSRFToken: <CSRFTOKEN>"
            -H "Content-Type: application/json" \
            -i http://127.0.0.1:8000/session_login/
    """

    if request.method == 'POST':
        data = JSONParser().parse(request)
        sess_code = data.get('sess', None)
        username = data.get('username', None)
        password = data.get('password', None)

        if sess_code and username and password:
            app_sess_obj = get_object_or_404(ApplicationSession, code=sess_code)

            if app_sess_obj.auth_mode == 'login':
                user_obj = get_object_or_404(User, username=username)

                if user_obj.check_password(password):
                    app_config_obj, user_sess_obj = _generate_user_session(app_sess_obj, user_obj)

                    return JsonResponse({
                        'sess_code': app_sess_obj.code,
                        'auth_mode': app_sess_obj.auth_mode,
                        'user_code': user_sess_obj.code,
                        'config': app_config_obj.config
                    }, status=201)
                else:
                    return HttpResponse(status=401)

    return HttpResponse(status=400)


@require_user_session_token
def start_tracking(request, user_app_sess_obj, parsed_data):
    """
    Start a tracking session within a user application session.

    Checking this view locally with `curl`:

    1. See how to obtain a user application session token `<AUTH_TOKEN>` and all other required codes/tokens in the
       docs for `app_session_login` (when login is required) or `app_session` (when no login is required).

    2. Then run the following (note that you can additionally pass "device_info" JSON data):

        curl -d '{"sess": "<SESS_CODE>", "start_time": "<START_TIME>"}' \
             -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -H "X-CSRFToken: <CSRFTOKEN>" \
             -H "Authorization: Token <AUTH_TOKEN>" \
             -H "Content-Type: application/json" \
             -i http://127.0.0.1:8000/start_tracking/

    """
    if request.method == 'POST':
        parsed_data['user_app_session'] = user_app_sess_obj.pk

        try:
            # there already exists a tracking session for this user session
            tracking_sess_obj = TrackingSession.objects.get(user_app_session=user_app_sess_obj)
            return JsonResponse({'tracking_session_id': tracking_sess_obj.pk}, status=200)
        except TrackingSession.DoesNotExist:
            # create a new tracking session
            if 'end_time' not in parsed_data and 'id' not in parsed_data:
                tracking_sess_serializer = TrackingSessionSerializer(data=parsed_data)
                if tracking_sess_serializer.is_valid():
                    tracking_sess_serializer.save()
                    return JsonResponse({'tracking_session_id': tracking_sess_serializer.instance.pk}, status=201)

    return HttpResponse(status=400)


@require_user_session_token
@require_tracking_session
def stop_tracking(request, user_app_sess_obj, parsed_data, tracking_sess_obj):
    """
    End a tracking session within a user application session.

    Checking this view locally with `curl`:

    1. See how to obtain a tracking session ID `<TRACKING_SESSION_ID>` and all other required codes/tokens in the
       docs for `start_tracking`.

    2. Then run the following:

        curl -d '{"sess": "<SESS_CODE>", "tracking_session_id": "<TRACKING_SESSION_ID>", "end_time": "<END_TIME>"}' \
             -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -H "X-CSRFToken: <CSRFTOKEN>" \
             -H "Authorization: Token <AUTH_TOKEN>" \
             -H "Content-Type: application/json" \
             -i http://127.0.0.1:8000/stop_tracking/

    """
    if request.method == 'POST' and 'end_time' in parsed_data:
        tracking_sess_serializer = TrackingSessionSerializer(tracking_sess_obj,
                                                             data={'end_time': parsed_data['end_time']},
                                                             partial=True)
        if tracking_sess_serializer.is_valid():
            tracking_sess_serializer.save()
            return JsonResponse({'tracking_session_id': tracking_sess_serializer.instance.pk}, status=200)

    return HttpResponse(status=400)


@require_user_session_token
@require_tracking_session
def track_event(request, user_app_sess_obj, parsed_data, tracking_sess_obj):
    """
    Track an event.

    Checking this view locally with `curl`:

    1. See how to obtain a tracking session ID `<TRACKING_SESSION_ID>` and all other required codes/tokens in the
       docs for `start_tracking`.

    2. Then run the following:

        curl -d '{"sess": "<SESS_CODE>", "tracking_session_id": "<TRACKING_SESSION_ID>", \
                  "event": {"time": "<EVENT_TIME>", "type": "<EVENT_TYPE>"}}' \
             -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -H "X-CSRFToken: <CSRFTOKEN>" \
             -H "Authorization: Token <AUTH_TOKEN>" \
             -H "Content-Type: application/json" \
             -i http://127.0.0.1:8000/track_event/

    """
    if request.method == 'POST':
        data = parsed_data.get('event', {})
        data['tracking_session'] = tracking_sess_obj.pk
        event_serializer = TrackingEventSerializer(data=data)

        if event_serializer.is_valid():
            event_serializer.save()
            return JsonResponse({'tracking_event_id': event_serializer.instance.pk}, status=201)

    return HttpResponse(status=400)


def csrf_failure(request, reason=""):
    """
    Default CSRF failure view to format a CSRF failure as JSON.
    """
    return JsonResponse({'error': 'CSRF check failure.', 'reason': reason}, status=401)


# --- helpers ---


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
