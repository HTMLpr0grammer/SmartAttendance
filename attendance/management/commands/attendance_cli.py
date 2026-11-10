"""python manage.py attendance_cli – interactive command-line menu for SmartAttendance."""
from django.core.management.base import BaseCommand

from attendance.cli import AttendanceCLI


class Command(BaseCommand):
    help = ("Interactive text menu: add, view, search, update and archive students, enroll and drop, record "
            "attendance, rankings, counts, the unenrolled-scan queue and an algorithm demonstration.")

    def handle(self, *args, **options):
        AttendanceCLI().run()   # reads the keyboard and prints to the terminal
