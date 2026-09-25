from django.utils import translation


class AdminLanguageMiddleware:
    """Admin language from the language cookie / browser.

    With i18n_patterns(prefix_default_language=False) Django's LocaleMiddleware forces the default language on every
    unprefixed URL, including /admin/. The admin has no language prefix, so pick its language the usual way here.
    """

    PREFIXES = ("/admin/",)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info.startswith(self.PREFIXES):
            lang = translation.get_language_from_request(request, check_path=False)
            translation.activate(lang)
            request.LANGUAGE_CODE = translation.get_language()
        return self.get_response(request)
