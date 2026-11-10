# Contents for PythonAnywhere's WSGI file.
# On the Web tab, click the link next to "WSGI configuration file"
# (/var/www/<yourname>_pythonanywhere_com_wsgi.py), delete everything in it,
# paste this in, change YOURNAME, and save.
#
# Secrets are NOT set here: settings.py reads them from the .env file next to manage.py.
import os
import sys

PROJECT_DIR = "/home/YOURNAME/SmartAttendance"
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

os.environ["DJANGO_SETTINGS_MODULE"] = "smartattendance.settings"

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
