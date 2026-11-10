from django.conf import settings


def app_context(request):
    conf = settings.SMARTATTENDANCE
    return {
        "APP_NAME": conf["APP_NAME"],
        "SCHOOL_NAME": conf["SCHOOL_NAME"],
        "SCHOOL_SHORT": conf["SCHOOL_SHORT"],
    }
