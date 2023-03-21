from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from .models import ApplicationSession, ApplicationConfig, UserApplicationSession, User


def app_session(request):
    if request.method == 'GET':
        sess_code = request.GET.get('sess', None)
        if sess_code:
            app_sess_obj = get_object_or_404(ApplicationSession, code=sess_code)
            response_data = {'sess_code': app_sess_obj.code, 'auth_mode': app_sess_obj.auth_mode}

            if app_sess_obj.auth_mode == 'none':
                app_config_obj, user_sess_obj = _generate_user_session(app_sess_obj)    # user_id will stay None

                response_data.update({
                    'user_code': user_sess_obj.code,
                    'config': app_config_obj.config
                })

                status = 201
            else:
                status = 200

            return JsonResponse(response_data, status=status)

    return HttpResponse(status=400)


def app_session_login(request):
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
                    'config': app_config_obj
                }, status=201)

    return HttpResponse(status=400)


def _generate_user_session(app_sess_obj, user_obj=None):
    app_config_obj = get_object_or_404(ApplicationConfig, id=app_sess_obj.config_id)

    user_sess_obj = UserApplicationSession(application_session=app_sess_obj, user=user_obj)
    user_sess_obj.generate_code()
    user_sess_obj.save(force_insert=True)

    return app_config_obj, user_sess_obj
