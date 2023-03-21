from django.http import HttpResponse, JsonResponse

from .models import ApplicationSession, ApplicationConfig, UserApplicationSession


def app_session(request):
    if request.method == 'GET':
        sess_code = request.GET.get('sess', None)
        if sess_code:
            try:
                sess_obj = ApplicationSession.objects.get(code=sess_code)
            except ApplicationSession.DoesNotExist:
                return HttpResponse(status=404)

            response_data = {'sess_code': sess_obj.code, 'auth_mode': sess_obj.auth_mode}

            if sess_obj.auth_mode == 'none':
                try:
                    app_config = ApplicationConfig.objects.get(id=sess_obj.config_id)
                except ApplicationConfig.DoesNotExist:
                    return HttpResponse(status=404)

                user_sess_obj = UserApplicationSession(application_session=sess_obj)  # user_id will stay None
                user_sess_code = user_sess_obj.generate_code()
                user_sess_obj.save(force_insert=True)

                response_data.update({
                    'user_code': user_sess_code,
                    'config': app_config.config
                })

                status = 201
            else:
                status = 200

            return JsonResponse(response_data, status=status)

    return HttpResponse(status=401)
