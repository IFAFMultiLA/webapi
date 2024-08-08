"""
Common utilities.

.. codeauthor:: Markus Konrad <markus.konrad@htw-berlin.de>
"""

from django.conf import settings
from django.utils.deprecation import MiddlewareMixin


class DisableCSRF(MiddlewareMixin):
    def process_request(self, request):
        if settings.DEBUG:
            setattr(request, "_dont_enforce_csrf_checks", True)
