"""
Views that expose the API endpoints.

API endpoints that accept data via POST must pass the data in JSON format. When dates or times are passed, they must
be formatted in ISO 8601 format.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

import logging
import re
import string
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.utils import IntegrityError
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import defaults as default_views
from django.views.csrf import csrf_failure as default_csrf_failure
from django.views.decorators.csrf import ensure_csrf_cookie
from ipware import get_client_ip
from rest_framework import status
from rest_framework.decorators import api_view

if settings.CHATBOT_API:
    from .chatapi import new_chat_api

from .models import (
    Application,
    ApplicationConfig,
    ApplicationSession,
    ApplicationSessionGate,
    TrackingEvent,
    TrackingSession,
    User,
    UserApplicationSession,
    UserFeedback,
)
from .serializers import TrackingEventSerializer, TrackingSessionSerializer, UserFeedbackSerializer

# --- constants ---

DEFAULT_ERROR_VIEWS = {
    status.HTTP_400_BAD_REQUEST: ("Bad request.", default_views.bad_request),
    status.HTTP_403_FORBIDDEN: ("Permission denied.", default_views.permission_denied),
    status.HTTP_404_NOT_FOUND: ("Not found.", default_views.page_not_found),
    status.HTTP_500_INTERNAL_SERVER_ERROR: ("Server error.", default_views.server_error),
}

MIN_PASSWORD_LENGTH = 8
if settings.CHATBOT_API and "content_section_identifier_pattern" in settings.CHATBOT_API.keys():
    PTTRN_CHATBOT_RESPONSE_SECTION = re.compile(settings.CHATBOT_API["content_section_identifier_pattern"])
else:
    PTTRN_CHATBOT_RESPONSE_SECTION = None

logger = logging.getLogger(__name__)

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
        auth_data = request.headers.get("authorization", "").split(" ")  # expected format: "Token <token code>"
        if len(auth_data) == 2 and auth_data[0].lower() == "token":
            # get the token
            token = auth_data[1]

            # get the application session code
            sess = request.data.get("sess", None) or request.query_params.get("sess", None)

            if token and sess:
                try:
                    # get the user application session for the given application session and user session token
                    user_app_sess_obj = UserApplicationSession.objects.get(application_session_id=sess, code=token)
                except UserApplicationSession.DoesNotExist:
                    return HttpResponse(status=status.HTTP_401_UNAUTHORIZED)

                if user_app_sess_obj.application_session.auth_mode == "login" and not user_app_sess_obj.user:
                    # login requires a user object
                    return HttpResponse(status=status.HTTP_401_UNAUTHORIZED)
                elif user_app_sess_obj.application_session.auth_mode == "none" and user_app_sess_obj.user:
                    # when auth mode is "none", the user should be anonymous
                    raise RuntimeError(
                        'application session authentication mode is "none" but user is authenticated '
                        f'as "{user_app_sess_obj.user}"'
                    )

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
        tracking_session_id = parsed_data.get("tracking_session_id", None)

        if tracking_session_id:
            try:
                # get tracking session for this user application session
                tracking_sess_obj = TrackingSession.objects.get(
                    id=tracking_session_id, user_app_session_id=user_app_sess_obj.pk, end_time__isnull=True
                )  # tracking session still active
            except TrackingSession.DoesNotExist:
                return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

            return view_fn(
                request,
                user_app_sess_obj=user_app_sess_obj,
                parsed_data=parsed_data,
                tracking_sess_obj=tracking_sess_obj,
            )

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

    return wrap


# --- views ---


def index(request):
    return render(request, "index.html")


@ensure_csrf_cookie
@api_view(["GET"])
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

    Checking this view locally with `curl`::

        curl -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -i http://127.0.0.1:8000/session/?sess=<SESS_CODE>

    Returns a JSON response with:

    - `sess_code`: session code
    - `active`: boolean value indicating if the application session is active
    - optional `auth_mode`: authentication mode ("none" or "login") if `sess` was passed
    - optional `user_code`: generated user authentication token (when `auth_mode` is "none")
    - optional `config`: application configuration as JSON object (when `auth_mode` is "none")
    """

    if request.method == "GET":
        sess_code = request.GET.get("sess", None)
        response_data = None
        return_status = None
        if sess_code:
            # get the application session
            app_sess_obj = get_object_or_404(ApplicationSession, code=sess_code)
            response_data = {
                "sess_code": app_sess_obj.code,
                "auth_mode": app_sess_obj.auth_mode,
                "active": app_sess_obj.is_active,
            }

            if app_sess_obj.auth_mode == "none" and app_sess_obj.is_active:
                # create a user code
                app_config_obj, user_sess_obj = _generate_user_session(app_sess_obj)  # user_id will stay None

                # set additional response data
                response_data.update({"user_code": user_sess_obj.code, "config": app_config_obj.config})

                return_status = status.HTTP_201_CREATED
            else:
                # app session is inactive or user must login; no additional response data
                return_status = status.HTTP_200_OK
        elif referrer := request.GET.get("referrer", request.META.get("HTTP_REFERER", None)):
            default_app_sessions = Application.objects.filter(default_application_session__isnull=False).values(
                "url", "default_application_session__is_active", "default_application_session__code"
            )

            for app_sess in default_app_sessions:
                if app_sess["url"] == referrer or (referrer.endswith("/") and app_sess["url"] + "/" == referrer):
                    response_data = {
                        "sess_code": app_sess["default_application_session__code"],
                        "active": app_sess["default_application_session__is_active"],
                    }
                    return_status = status.HTTP_200_OK
                    break

        if response_data and return_status:
            return JsonResponse(response_data, status=return_status)

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
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
    2. Then run the following::

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

    if request.method == "POST":
        data = request.data
        sess_code = data.get("sess", None)
        username = data.get("username", None)
        email = data.get("email", None)
        password = data.get("password", None)

        if sess_code and (username or email) and password:
            app_sess_obj = get_object_or_404(ApplicationSession, code=sess_code)

            if app_sess_obj.auth_mode == "login" and app_sess_obj.is_active:
                ident_args = {}
                if username:
                    ident_args["username"] = username
                if email:
                    ident_args["email"] = email
                assert ident_args
                user_obj = get_object_or_404(User, **ident_args)

                if user_obj.check_password(password):
                    app_config_obj, user_sess_obj = _generate_user_session(app_sess_obj, user_obj)

                    return JsonResponse(
                        {
                            "sess_code": app_sess_obj.code,
                            "auth_mode": app_sess_obj.auth_mode,
                            "user_code": user_sess_obj.code,
                            "config": app_config_obj.config,
                        },
                        status=status.HTTP_201_CREATED,
                    )
                else:
                    return HttpResponse(status=status.HTTP_401_UNAUTHORIZED)
        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
def register_user(request):
    """
    Register a new user with a username and/or email address and password. The following data must be provided via POST:

    - `username` (optional if email is given)
    - `email` (optional if username is given)
    - `password`

    Checking this view locally with `curl`::

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
        return JsonResponse({"error": err, "message": msg}, status=status.HTTP_403_FORBIDDEN)

    if request.method == "POST":
        data = request.data
        username = data.get("username", None)
        email = data.get("email", None)
        password = data.get("password", None)

        if (username or email) and password:
            if email:
                # if an email address is given, check its format
                try:
                    validate_email(email)
                except ValidationError:
                    return _validation_error("invalid_email", "The provided email address seems invalid.")

            # we require only very few, very basic password safety checks as a password breach does not impose a high
            # risk in this application
            if len(password) < MIN_PASSWORD_LENGTH:
                return _validation_error(
                    "pw_too_short", f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
                )
            if password == username:
                return _validation_error("pw_same_as_user", "Password must be different from provided username.")
            if password == email:
                return _validation_error("pw_same_as_email", "Password must be different from provided email.")

            # try to create the account
            try:
                User.objects.create_user(username or email, email=email, password=password)
            except IntegrityError:  # oops, already exists
                return _validation_error("user_already_registered", "This account already exists.")

            return HttpResponse(status=status.HTTP_201_CREATED)

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST", "GET"])
@require_user_session_token
def user_feedback(request, user_app_sess_obj, parsed_data):
    """
    Either POST new user feedback data or GET existing user feedback data within a user application session and
    optionally within a tracking session.

    Checking this view locally with `curl`:

    1. See how to obtain a user application session token `<AUTH_TOKEN>` and all other required codes/tokens in the
       docs for `app_session_login` (when login is required) or `app_session` (when no login is required).
    2. Optional: See how to obtain a tracking session ID `<TRACKING_SESSION_ID>` and all other required codes/tokens in
       the docs for `start_tracking`.
    3. Run the following (the "tracking_session" part is optional; one of "score" and "text" is optional) to post new
       user feedback data::

        curl -d '{"sess": "<SESS_CODE>", "tracking_session": "<TRACKING_SESSION_ID>",
                  "content_section": "<CONTENT_SECTION>", "score": <SCORE>, "text": "<COMMENT>"}' \
             -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -H "X-CSRFToken: <CSRFTOKEN>" \
             -H "Authorization: Token <AUTH_TOKEN>" \
             -H "Content-Type: application/json" \
             -i http://127.0.0.1:8000/user_feedback/

    4. Run the following to fetch existing user feedback data for the current user application session::

        curl -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -H "X-CSRFToken: <CSRFTOKEN>" \
             -H "Authorization: Token <AUTH_TOKEN>" \
             -H "Content-Type: application/json" \
             -i http://127.0.0.1:8000/user_feedback/?sess=<SESS_CODE>

    For POSTing new user feedback, this view returns an HTTP 201 response on success.

    For GETting existing user feedback, this view returns an HTTP 200 response on success with the following data as
    JSON::

        {
            "user_feedback": [
                {
                    "content_section": "<CONTENT_SECTION>",
                    "score": <SCORE>,
                    "text": "<COMMENT>"
                },
                ...  // more user feedback for each content section
            ]
        }
    """

    if request.method == "POST":
        # post new user feedback
        parsed_data["user_app_session"] = user_app_sess_obj.pk

        # get the app. config. for this app. session
        app_config = user_app_sess_obj.application_session.config.config

        # remove data that should not be there according to the configuration
        if not app_config.get("feedback", True):
            if "score" in parsed_data:
                del parsed_data["score"]
            if "text" in parsed_data:
                del parsed_data["text"]

        # if we have a tracking session, make sure it belongs to the user app. session
        if tracking_session := parsed_data.get("tracking_session", None):
            try:
                tracking_sess = TrackingSession.objects.get(id=tracking_session)
                if tracking_sess.user_app_session_id != user_app_sess_obj.pk:
                    return HttpResponse(status=status.HTTP_400_BAD_REQUEST)
            except TrackingSession.DoesNotExist:
                return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

        # see if we already have an existing user feedback instance to perform an update
        try:
            user_feedback_obj = UserFeedback.objects.get(
                user_app_session=parsed_data["user_app_session"],
                content_section=parsed_data.get("content_section", None),
            )

            # perform an update
            response_status = status.HTTP_200_OK
        except UserFeedback.DoesNotExist:
            # create a new user feedback instance
            user_feedback_obj = None
            response_status = status.HTTP_201_CREATED

        # serialize and validate passed data
        user_feedback_serializer = UserFeedbackSerializer(user_feedback_obj, data=parsed_data)

        if user_feedback_serializer.is_valid():
            try:
                # store to DB
                with transaction.atomic():
                    user_feedback_serializer.save()
            except IntegrityError:  # DB constrain failed
                return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

            # all OK
            return HttpResponse(status=response_status)
        else:
            return JsonResponse(
                {"validation_errors": user_feedback_serializer.errors}, status=status.HTTP_400_BAD_REQUEST
            )
    elif request.method == "GET":
        # get existing user feedback
        user_feedback = UserFeedback.objects.filter(user_app_session=user_app_sess_obj).values(
            "content_section", "score", "text"
        )

        return JsonResponse({"user_feedback": list(user_feedback)}, status=status.HTTP_200_OK)


@api_view(["POST"])
@require_user_session_token
def start_tracking(request, user_app_sess_obj, parsed_data):
    """
    Start a tracking session within a user application session.

    Checking this view locally with `curl`:

    1. See how to obtain a user application session token `<AUTH_TOKEN>` and all other required codes/tokens in the
       docs for `app_session_login` (when login is required) or `app_session` (when no login is required).
    2. Then run the following (note that you can additionally pass "device_info" JSON data)::

        curl -d '{"sess": "<SESS_CODE>", "start_time": "<START_TIME>"}' \
             -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -H "X-CSRFToken: <CSRFTOKEN>" \
             -H "Authorization: Token <AUTH_TOKEN>" \
             -H "Content-Type: application/json" \
             -i http://127.0.0.1:8000/start_tracking/

    Returns a JSON response with:

    - `tracking_session_id`: tracking session ID for started tracking session
    """
    if request.method == "POST":
        parsed_data["user_app_session"] = user_app_sess_obj.pk

        try:
            # there already exists a tracking session for this user session
            tracking_sess_obj = TrackingSession.objects.get(user_app_session=user_app_sess_obj, end_time__isnull=True)
            return JsonResponse({"tracking_session_id": tracking_sess_obj.pk}, status=status.HTTP_200_OK)
        except TrackingSession.DoesNotExist:
            # create a new tracking session
            if "end_time" not in parsed_data and "id" not in parsed_data:
                if "device_info" not in parsed_data:
                    parsed_data["device_info"] = {}
                # find out client IP and store it in "device info" JSON data
                client_ip, _ = get_client_ip(request)
                app_config = user_app_sess_obj.application_session.config.config.get("tracking", {})
                parsed_data["device_info"]["client_ip"] = client_ip if app_config.get("ip", True) else None

                tracking_sess_serializer = TrackingSessionSerializer(data=parsed_data)
                if tracking_sess_serializer.is_valid():
                    tracking_sess_serializer.save()
                    return JsonResponse(
                        {"tracking_session_id": tracking_sess_serializer.instance.pk}, status=status.HTTP_201_CREATED
                    )
                else:
                    return JsonResponse(
                        {"validation_errors": tracking_sess_serializer.errors}, status=status.HTTP_400_BAD_REQUEST
                    )
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@require_user_session_token
@require_tracking_session
def stop_tracking(request, user_app_sess_obj, parsed_data, tracking_sess_obj):
    """
    End a tracking session within a user application session.

    Checking this view locally with `curl`:

    1. See how to obtain a tracking session ID `<TRACKING_SESSION_ID>` and all other required codes/tokens in the
       docs for `start_tracking`.
    2. Then run the following::

        curl -d '{"sess": "<SESS_CODE>", "tracking_session_id": "<TRACKING_SESSION_ID>", "end_time": "<END_TIME>"}' \
             -c /tmp/cookies.txt -b /tmp/cookies.txt \
             -H "X-CSRFToken: <CSRFTOKEN>" \
             -H "Authorization: Token <AUTH_TOKEN>" \
             -H "Content-Type: application/json" \
             -i http://127.0.0.1:8000/stop_tracking/

    Returns a JSON response with:

    - `tracking_session_id`: stopped tracking session ID (same as in request)
    """
    if request.method == "POST":
        if "end_time" in parsed_data:
            tracking_sess_serializer = TrackingSessionSerializer(
                tracking_sess_obj, data={"end_time": parsed_data["end_time"]}, partial=True
            )
            if tracking_sess_serializer.is_valid():
                tracking_sess_serializer.save()
                return JsonResponse(
                    {"tracking_session_id": tracking_sess_serializer.instance.pk}, status=status.HTTP_200_OK
                )
            else:
                return JsonResponse(
                    {"validation_errors": tracking_sess_serializer.errors}, status=status.HTTP_400_BAD_REQUEST
                )

        return HttpResponse(status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@require_user_session_token
@require_tracking_session
def track_event(request, user_app_sess_obj, parsed_data, tracking_sess_obj):
    """
    Track an event.

    Checking this view locally with `curl`:

    1. See how to obtain a tracking session ID `<TRACKING_SESSION_ID>` and all other required codes/tokens in the
       docs for `start_tracking`.
    2. Then run the following::

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
    if request.method == "POST":
        data = parsed_data.get("event", {})
        data["tracking_session"] = tracking_sess_obj.pk
        event_serializer = TrackingEventSerializer(data=data)

        if event_serializer.is_valid():
            event_serializer.save()
            return JsonResponse({"tracking_event_id": event_serializer.instance.pk}, status=status.HTTP_201_CREATED)
        else:
            return JsonResponse({"validation_errors": event_serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
@require_user_session_token
def chatbot_message(request, user_app_sess_obj, parsed_data):
    if not settings.CHATBOT_API:
        logger.error("Chatbot message endpoint used, but chatbot API not enabled.")
        return HttpResponse(status=status.HTTP_404_NOT_FOUND)

    # check if chatbot feature is enabled for this app config
    app_config = user_app_sess_obj.application_session.config
    if not (provider_model := app_config.config.get("chatbot", None)):
        logger.error("Chatbot message endpoint used, but chatbot API not configured for this application session.")
        return HttpResponse(status=status.HTTP_404_NOT_FOUND)

    # create a chat API instance with the respective settings
    chat_provider_label, chat_model = provider_model.split(" | ")
    try:
        chat_provider_opts = settings.CHATBOT_API["providers"][chat_provider_label]

        chat_api = new_chat_api(
            chat_provider_opts["provider"],
            chat_model,
            chat_provider_opts["key"],
            **chat_provider_opts.get("setup_options", {}),
        )
    except Exception as exc:
        logger.error('Error creating chatbot API object for label "%s": %s', chat_provider_label, exc)
        return HttpResponse(status=status.HTTP_503_SERVICE_UNAVAILABLE)

    # post new message to chatbot API
    if request.method == "POST":
        # get the user's message
        try:
            message = parsed_data["message"]
        except KeyError:
            logger.error("No message provided in chatbot endpoint.")
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

        # get optional data
        language = parsed_data.get("language", "en")
        simulate_chatapi = parsed_data.get("simulate", False)

        # if we have a tracking session, make sure it belongs to the user app. session
        tracking_sess = None
        if tracking_session_id := parsed_data.get("tracking_session", None):
            try:
                tracking_sess = TrackingSession.objects.get(id=tracking_session_id)
                if tracking_sess.user_app_session_id != user_app_sess_obj.pk:
                    return HttpResponse(status=status.HTTP_400_BAD_REQUEST)
            except TrackingSession.DoesNotExist:
                return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

        # get previous messages
        prev_comm = user_app_sess_obj.chatbot_communication or []

        # create prompt
        try:
            if app_config.config["chatbot_system_prompt"]:
                sys_prompt = app_config.config["chatbot_system_prompt"]
            else:
                sys_prompt = settings.CHATBOT_API["system_role_templates"][language]

            if app_config.config["chatbot_user_prompt"]:
                usr_prompt = app_config.config["chatbot_user_prompt"]
            else:
                usr_prompt = settings.CHATBOT_API["user_role_templates"][language]

            sys_role_templ = string.Template(sys_prompt)
            usr_role_templ = string.Template(usr_prompt)
        except KeyError as exc:
            logger.error("Error creating chatbot prompt: %s", exc)
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

        app_content = app_config.app_content
        if app_content is None:
            logger.error("No app content provided for chatbot prompt.")
            return HttpResponse(status=status.HTTP_204_NO_CONTENT)
        sys_role = sys_role_templ.substitute(doc_text=app_content)
        prompt = usr_role_templ.substitute(doc_text=app_content, question=message)

        if not prompt:
            logger.error("No prompt provided.")
            return HttpResponse(status=status.HTTP_400_BAD_REQUEST)

        msgs_for_request = [
            {"role": "system", "content": sys_role},
        ]

        for role, msg in prev_comm:
            msgs_for_request.append({"role": role, "content": msg})

        msgs_for_request.append({"role": "user", "content": prompt})

        # make an request to the API
        if simulate_chatapi:
            # only simulate the request
            msgs_formatted = "\n\n".join(f'{msg["role"]}: {msg["content"]}' for msg in msgs_for_request)

            bot_response = (
                f"No real chat response was generated since the chat API request to provider with label "
                f"'{chat_provider_label}' and model '{chat_model}' was only simulated with the following "
                f"messages:\n\n{msgs_formatted}\n\nmainContentElem-13"
            )

            if isinstance(simulate_chatapi, str):
                bot_response = bot_response + f"\n\n{simulate_chatapi}"
        else:
            try:
                # make a real request and get the API response
                bot_response = chat_api.request(msgs_for_request, **chat_provider_opts.get("request_options", {}))

                if not bot_response:
                    logger.error("No response returned from chat API.")
                    return HttpResponse(status=status.HTTP_503_SERVICE_UNAVAILABLE)
            except Exception as exc:
                logger.error("An error occurred while sending a request to the chat API: %s", exc)
                return HttpResponse(status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # post-process bot response
        bot_response = bot_response.strip()
        if PTTRN_CHATBOT_RESPONSE_SECTION and (m := PTTRN_CHATBOT_RESPONSE_SECTION.search(bot_response)):
            content_section = m.group(0)
            bot_response = PTTRN_CHATBOT_RESPONSE_SECTION.sub("", bot_response).strip()
        else:
            content_section = None

        # save this communication to the DB
        prev_comm.extend([["user", prompt], ["assistant", bot_response]])
        user_app_sess_obj.chatbot_communication = prev_comm
        user_app_sess_obj.save()

        # also store as tracking event if tracking is enabled
        if tracking_sess and app_config.config.get("tracking", {}).get("chatbot", False):
            event = TrackingEvent(
                tracking_session=tracking_sess,
                time=datetime.now(ZoneInfo(settings.TIME_ZONE)),
                type="chatbot_communication",
                value={
                    "user": prompt,
                    "assistant": bot_response,
                    "assistant_content_section_ref": content_section,
                    "model": provider_model,
                },
            )
            event.save()

        # return response as JSON
        return JsonResponse({"message": bot_response, "content_section": content_section}, status=status.HTTP_200_OK)


def csrf_failure(request, reason=""):
    """
    Default CSRF failure view to format a CSRF failure as JSON.
    """
    if request.headers.get("content-type", None) == "application/json":
        return JsonResponse({"error": "CSRF check failure.", "reason": reason}, status=status.HTTP_401_UNAUTHORIZED)
    else:
        return default_csrf_failure(request, reason=reason)


def bad_request_failure(request, exception, template_name="status.HTTP_400_BAD_REQUEST.html"):
    """HTTP status status.HTTP_400_BAD_REQUEST / bad request response view."""
    return _wrap_failure_request(request, status.HTTP_400_BAD_REQUEST, exception=exception, template_name=template_name)


def permission_denied_failure(request, exception, template_name="403.html"):
    """HTTP status 403 / permission denied response view."""
    return _wrap_failure_request(request, status.HTTP_403_FORBIDDEN, exception=exception, template_name=template_name)


def not_found_failure(request, exception, template_name="404.html"):
    """HTTP status 404 / not found response view."""
    return _wrap_failure_request(request, status.HTTP_404_NOT_FOUND, exception=exception, template_name=template_name)


def server_error_failure(request, template_name="500.html"):
    """HTTP status 500 / server failure response view."""
    return _wrap_failure_request(request, status.HTTP_500_INTERNAL_SERVER_ERROR, template_name=template_name)


# --- non-API views ---


def app_session_gate(request, sessioncode):
    """
    Session gate redirect. Look up an application session or an application session *gate* with code `sessioncode`,
    select the next app session at the next redirection index and redirect it to that URL. Increase the next redirection
    index by 1.
    """

    cookie_key = "gate_app_sess_" + sessioncode
    app_sess = None
    set_cookie = False

    try:
        # first, try to fetch an application session with that code ...
        app_sess = ApplicationSession.objects.get(code=sessioncode)
    except ApplicationSession.DoesNotExist:
        # ... if that fails, fetch an application session gate with that code
        set_cookie = True
        with transaction.atomic():
            # get the app. session gate and its active app sessions
            gate = get_object_or_404(ApplicationSessionGate, code=sessioncode)
            if not gate.is_active:  # the gate is not active -> show a message
                return render(request, "app_sess_inactive.html")

            active_app_sessions = gate.app_sessions.filter(is_active=True)

            # make sure it has at least 1 active app session assigned
            n_app_sess = active_app_sessions.count()
            if n_app_sess <= 0:
                return HttpResponse(status=status.HTTP_204_NO_CONTENT)

            # try to get the app. session from the cookie
            if app_sess_from_cookie := request.COOKIES.get(cookie_key, None):
                try:
                    app_sess = active_app_sessions.get(code=app_sess_from_cookie)
                except ApplicationSession.DoesNotExist:
                    pass

            if app_sess is None:  # first visit – app. session not already stored in a cookie
                # get the redirect URL at the current index
                index = min(n_app_sess - 1, gate.next_forward_index)
                app_sess = active_app_sessions.order_by("code")[index]

                # increase the next redirection index by 1 within bounds [0, <number of app. sessions>] in order to
                # visit one app session after another, e.g. A -> B -> A -> B -> ... for a gate with 2 app sessions
                gate.next_forward_index = (index + 1) % n_app_sess
                gate.save()

    if app_sess:
        if app_sess.is_active:
            # set cookie and redirect to app. session
            response = HttpResponseRedirect(app_sess.session_url())
            if set_cookie:
                response.set_cookie(cookie_key, app_sess.code, max_age=60 * 60 * 12, secure=True)
            return response
        else:
            return render(request, "app_sess_inactive.html")

    # something went wrong
    return HttpResponse(status=status.HTTP_404_NOT_FOUND)


# --- helpers ---


def _wrap_failure_request(request, status, **kwargs):
    """
    Wrap a failure request and generate either a JSON response (when the request was a JSON request) or the django
    standard HTML response.
    """
    error_msg, default_view = DEFAULT_ERROR_VIEWS[status]
    if request.headers.get("content-type", None) == "application/json":
        return JsonResponse({"error": error_msg}, status=status)
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
