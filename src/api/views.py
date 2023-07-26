"""
Views that expose the API endpoints.

API endpoints that accept data via POST must pass the data in JSON format. When dates or times are passed, they must
be formatted in ISO 8601 format.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

from functools import wraps

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views import defaults as default_views
from django.views.csrf import csrf_failure as default_csrf_failure
from django.db.utils import IntegrityError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import api_view

from .models import ApplicationSession, ApplicationConfig, UserApplicationSession, User, TrackingSession, Application, \
    UserFeedback
from .serializers import TrackingSessionSerializer, TrackingEventSerializer


# --- constants ---

DEFAULT_ERROR_VIEWS = {
    status.HTTP_400_BAD_REQUEST: ('Bad request.', default_views.bad_request),
    status.HTTP_403_FORBIDDEN: ('Permission denied.', default_views.permission_denied),
    status.HTTP_404_NOT_FOUND: ('Not found.', default_views.page_not_found),
    status.HTTP_500_INTERNAL_SERVER_ERROR: ('Server error.', default_views.server_error)
}

MIN_PASSWORD_LENGTH = 8


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

            # get the application session code
            sess = request.data.get('sess', None)

            if token and sess:
                try:
                    # get the user application session for the given application session and user session token
                    user_app_sess_obj = UserApplicationSession.objects.get(application_session_id=sess, code=token)
                except UserApplicationSession.DoesNotExist:
                    return HttpResponse(status=status.HTTP_401_UNAUTHORIZED)

                if user_app_sess_obj.application_session.auth_mode == 'login' and not user_app_sess_obj.user:
                    # login requires a user object
                    return HttpResponse(status=status.HTTP_401_UNAUTHORIZED)
                elif user_app_sess_obj.application_session.auth_mode == 'none' and user_app_sess_obj.user:
                    # when auth mode is "none", the user should be anonymous
                    raise RuntimeError('application session authentication mode is "none" but user is authenticated '
                                       f'as "{user_app_sess_obj.user}"')

                # set the authenticated user (will be None if auth_mode is "none")
                request.user = user_app_sess_obj.user
                return view_fn(request, *args, user_app_sess_obj=user_app_sess_obj, parsed_data=request.data, **kwargs)
            else:
                return HttpResponse(status=status.HTTP_400_BAD_REQUEST)
        return HttpResponse(status=status.HTTP_401_UNAUTHORIZED)

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
                return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

            return view_fn(request,
                           user_app_sess_obj=user_app_sess_obj,
                           parsed_data=parsed_data,
                           tracking_sess_obj=tracking_sess_obj)

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

    return wrap


# --- views ---


@ensure_csrf_cookie
@api_view(['GET'])
def app_session(request):
    """
    Request an application session identified via GET parameter `sess`. If an application session requires user
    authentication, this view will only return the `auth_mode` as `"login"` along with an CSRF cookie. In this case,
    the client must proceed with the `app_session_login` view. If an application session does not require user
    authentication, this view will additionally generate a user code `user_code` (that must be used as authentication
    token for further requests) and an application configuration.

    If no session code is passed via GET parameter `sess`, then it is tried to obtain the code of a default application
    session. This is done by checking if either a referrer passed via `referrer` GET parameter or HTTP "referer" header
    is listed in the URLs of the applications and if such an application has a default application session, its code is
    returned as `sess_code` in a JSON object.

    Checking this view locally with `curl`:

        curl -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -i http://127.0.0.1:8000/session/?sess=<SESS_CODE>

    Returns a JSON response with:

    - `sess_code`: session code
    - optional `auth_mode`: authentication mode ("none" or "login") if `sess` was passed
    - optional `user_code`: generated user authentication token (when `auth_mode` is "none")
    - optional `config`: application configuration as JSON object (when `auth_mode` is "none")
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

                return_status = status.HTTP_201_CREATED
            else:
                # user must login; no additional response data
                return_status = status.HTTP_200_OK

            return JsonResponse(response_data, status=return_status)
        elif referrer := request.GET.get('referrer', request.META.get('HTTP_REFERER', None)):
            default_app_sessions = Application.objects.filter(default_application_session__isnull=False)\
                .values('url', 'default_application_session__code')

            for app_sess in default_app_sessions:
                if app_sess['url'] == referrer or (referrer.endswith('/') and app_sess['url'] + '/' == referrer):
                    return JsonResponse({'sess_code': app_sess['default_application_session__code']},
                                        status=status.HTTP_200_OK)

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def app_session_login(request):
    """
    Log in for an application session that requires authentication. The following data must be provided via POST:

    - application session code as `sess`
    - either username as `username` or email as `email` (if both is given both must belong to the same user)
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

    Returns a JSON response with:

    - `sess_code`: session code
    - `auth_mode`: authentication mode ("none" or "login")
    - `user_code`: generated user authentication token
    - `config`: application configuration as JSON object
    """

    if request.method == 'POST':
        data = request.data
        sess_code = data.get('sess', None)
        username = data.get('username', None)
        email = data.get('email', None)
        password = data.get('password', None)

        if sess_code and (username or email) and password:
            app_sess_obj = get_object_or_404(ApplicationSession, code=sess_code)

            if app_sess_obj.auth_mode == 'login':
                ident_args = {}
                if username:
                    ident_args['username'] = username
                if email:
                    ident_args['email'] = email
                assert ident_args
                user_obj = get_object_or_404(User, **ident_args)

                if user_obj.check_password(password):
                    app_config_obj, user_sess_obj = _generate_user_session(app_sess_obj, user_obj)

                    return JsonResponse({
                        'sess_code': app_sess_obj.code,
                        'auth_mode': app_sess_obj.auth_mode,
                        'user_code': user_sess_obj.code,
                        'config': app_config_obj.config
                    }, status=status.HTTP_201_CREATED)
                else:
                    return HttpResponse(status=status.HTTP_401_UNAUTHORIZED)
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
def register_user(request):
    """
    Register a new user with a username and/or email address and password. The following data must be provided via POST:

    - `username` (optional if email is given)
    - `email` (optional if username is given)
    - `password`

    Checking this view locally with `curl`:

        curl -d '{"username": "<USER>", "email": "<EMAIL>", "password": "<PASSWORD>"}' \
            -H "Content-Type: application/json" \
            -i http://127.0.0.1:8000/register_user/

    A few basic password safety checks are performed and it is checked whether a user with this name already exists. If
    any of these checks fail, a 403 error is returned with the following data:

    - `error`: short error label such as "pw_too_short"
    - `message`: full error message

    If a user account was created successfully, an HTTP 201 response is returned.
    """
    def _validation_error(err, msg):
        return JsonResponse({'error': err, 'message': msg}, status=status.HTTP_403_FORBIDDEN)

    if request.method == 'POST':
        data = request.data
        username = data.get('username', None)
        email = data.get('email', None)
        password = data.get('password', None)

        if (username or email) and password:
            if email:
                # if an email address is given, check its format
                try:
                    validate_email(email)
                except ValidationError:
                    return _validation_error('invalid_email', 'The provided email address seems invalid.')

            # we require only very few, very basic password safety checks as a password breach does not impose a high
            # risk in this application
            if len(password) < MIN_PASSWORD_LENGTH:
                return _validation_error('pw_too_short',
                                         f'Password must be at least {MIN_PASSWORD_LENGTH} characters long.')
            if password == username:
                return _validation_error('pw_same_as_user', f'Password must be different from provided username.')
            if password == email:
                return _validation_error('pw_same_as_email', f'Password must be different from provided email.')

            # try to create the account
            try:
                User.objects.create_user(username or email, email=email, password=password)
            except IntegrityError:  # oops, already exists
                return _validation_error('user_already_registered', 'This account already exists.')

            return HttpResponse(status=status.HTTP_201_CREATED)

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@require_user_session_token
def user_feedback(request, user_app_sess_obj, parsed_data):
    if request.method == 'POST':
        parsed_data['user_app_session'] = user_app_sess_obj.pk





@api_view(['POST'])
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

    Returns a JSON response with:

    - `tracking_session_id`: tracking session ID for started tracking session
    """
    if request.method == 'POST':
        parsed_data['user_app_session'] = user_app_sess_obj.pk

        try:
            # there already exists a tracking session for this user session
            tracking_sess_obj = TrackingSession.objects.get(user_app_session=user_app_sess_obj, end_time__isnull=True)
            return JsonResponse({'tracking_session_id': tracking_sess_obj.pk}, status=status.HTTP_200_OK)
        except TrackingSession.DoesNotExist:
            # create a new tracking session
            if 'end_time' not in parsed_data and 'id' not in parsed_data:
                tracking_sess_serializer = TrackingSessionSerializer(data=parsed_data)
                if tracking_sess_serializer.is_valid():
                    tracking_sess_serializer.save()
                    return JsonResponse({'tracking_session_id': tracking_sess_serializer.instance.pk},
                                        status=status.HTTP_201_CREATED)
                else:
                    return JsonResponse({'validation_errors': tracking_sess_serializer.errors},
                                        status=status.HTTP_400_BAD_REQUEST)
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
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

    Returns a JSON response with:

    - `tracking_session_id`: stopped tracking session ID (same as in request)
    """
    if request.method == 'POST':
        if 'end_time' in parsed_data:
            tracking_sess_serializer = TrackingSessionSerializer(tracking_sess_obj,
                                                                 data={'end_time': parsed_data['end_time']},
                                                                 partial=True)
            if tracking_sess_serializer.is_valid():
                tracking_sess_serializer.save()
                return JsonResponse({'tracking_session_id': tracking_sess_serializer.instance.pk},
                                    status=status.HTTP_200_OK)
            else:
                return JsonResponse({'validation_errors': tracking_sess_serializer.errors},
                                    status=status.HTTP_400_BAD_REQUEST)

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
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

    Returns a JSON response with:

    - `tracking_event_id`: saved tracking event ID
    """
    if request.method == 'POST':
        data = parsed_data.get('event', {})
        data['tracking_session'] = tracking_sess_obj.pk
        event_serializer = TrackingEventSerializer(data=data)

        if event_serializer.is_valid():
            event_serializer.save()
            return JsonResponse({'tracking_event_id': event_serializer.instance.pk}, status=status.HTTP_201_CREATED)
        else:
            return JsonResponse({'validation_errors': event_serializer.errors},
                                status=status.HTTP_400_BAD_REQUEST)


def csrf_failure(request, reason=""):
    """
    Default CSRF failure view to format a CSRF failure as JSON.
    """
    if request.headers.get('content-type', None) == 'application/json':
        return JsonResponse({'error': 'CSRF check failure.', 'reason': reason}, status=status.HTTP_401_UNAUTHORIZED)
    else:
        return default_csrf_failure(request, reason=reason)


def bad_request_failure(request, exception, template_name='status.HTTP_400_BAD_REQUEST.html'):
    """HTTP status status.HTTP_400_BAD_REQUEST / bad request response view."""
    return _wrap_failure_request(request, status.HTTP_400_BAD_REQUEST, exception=exception, template_name=template_name)


def permission_denied_failure(request, exception, template_name='403.html'):
    """HTTP status 403 / permission denied response view."""
    return _wrap_failure_request(request, status.HTTP_403_FORBIDDEN, exception=exception, template_name=template_name)


def not_found_failure(request, exception, template_name='404.html'):
    """HTTP status 404 / not found response view."""
    return _wrap_failure_request(request, status.HTTP_404_NOT_FOUND, exception=exception, template_name=template_name)


def server_error_failure(request, template_name='500.html'):
    """HTTP status 500 / server failure response view."""
    return _wrap_failure_request(request, status.HTTP_500_INTERNAL_SERVER_ERROR, template_name=template_name)


# --- helpers ---


def _wrap_failure_request(request, status, **kwargs):
    """
    Wrap a failure request and generate either a JSON response (when the request was a JSON request) or the django
    standard HTML response.
    """
    error_msg, default_view = DEFAULT_ERROR_VIEWS[status]
    if request.headers.get('content-type', None) == 'application/json':
        return JsonResponse({'error': error_msg}, status=status)
    else:
        return default_view(request, **kwargs)


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
