"""
SmartAttendance command-line menu (`python manage.py attendance_cli`).

A text interface over the same database and business rules as the web application. It is meant for
administrators and for demonstrating the data structures and algorithms in attendance/dsa.py: every
list, search, ranking and count shown here is produced by those hand-written algorithms, and the menu
reports how many comparisons or steps each one took.

Records are never physically deleted: "delete" for a student means archiving the account
(soft delete), and for an enrollment it means dropping the student from the subject.
"""
import getpass
import math
import sys

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, IntegrityError, transaction
from django.utils.timezone import localtime

from . import benchmark
from .directory import StudentDirectory, full_name, split_identifiers
from .dsa import (
    HashIndex, OpCounter, QueueEmpty, binary_search, count_by, count_by_group, linear_search, merge_sort,
    normalize_key,
)
from .forms import RegistrationForm
from .models import AttendanceLog, AuditScanLog, ClassSession, StudentProfile, Subject, SubjectEnrollment, User
from .services import (
    ROSTER_SORTS, STATUSES, approve_rogue_scan, enroll_students, punctuality_summary, rank_by_attendance,
    rogue_scans_for_session, set_attendance, sort_roster, student_rows_with_standing, summary_from_counts,
    watchlist,
)

STATUS_CHOICES = {"p": "present", "l": "late", "a": "absent"}


class Quit(Exception):
    """Raised when the user leaves the program (Exit, end of input or Ctrl+C)."""


class AttendanceCLI:
    def __init__(self, stdin=None, stdout=None):
        self.stdin = stdin or sys.stdin
        self.out = stdout or sys.stdout
        self._directory = None

    # ------------------------------------------------------------------ I/O helpers
    def say(self, text=""):
        self.out.write(f"{text}\n")

    def _readline(self, prompt):
        self.out.write(prompt)
        self.out.flush()
        line = self.stdin.readline()
        if line == "":                       # end of input (Ctrl+D / Ctrl+Z)
            raise Quit
        return line.rstrip("\r\n")

    def ask(self, prompt, required=True, default=None, validate=None, max_length=150):
        """Ask until the answer is valid. *validate* returns an error message or None."""
        hint = f" [{default}]" if default not in (None, "") else ""
        while True:
            value = self._readline(f"{prompt}{hint}: ").strip()
            if not value and default is not None:
                return default
            if not value:
                if required:
                    self.say("  This field is required.")
                    continue
                return ""
            if len(value) > max_length:
                self.say(f"  Please keep it under {max_length} characters.")
                continue
            error = validate(value) if validate else None
            if error:
                self.say(f"  {error}")
                continue
            return value

    def ask_int(self, prompt, low, high, default=None):
        def check(v):
            if not v.isdigit() or not low <= int(v) <= high:
                return f"Enter a whole number from {low} to {high}."
            return None
        return int(self.ask(prompt, default=None if default is None else str(default), validate=check))

    def ask_secret(self, prompt):
        if hasattr(self.stdin, "isatty") and self.stdin.isatty():
            value = getpass.getpass(f"{prompt}: ")
        else:
            value = self._readline(f"{prompt}: ")
        return value

    def confirm(self, prompt):
        answer = self.ask(f"{prompt} (y/n)", validate=lambda v: None if v.lower() in ("y", "n", "yes", "no")
                          else "Please answer y or n.")
        return answer.lower().startswith("y")

    def choose(self, title, options):
        """Show a numbered menu and return the chosen key, or None for 0 (Back/Exit)."""
        self.say()
        self.say(title)
        self.say("-" * len(title))
        for number, (_, label) in enumerate(options, start=1):
            self.say(f"  {number}. {label}")
        self.say("  0. Back" if title != "MAIN MENU" else "  0. Exit")
        choice = self.ask_int("Choose", 0, len(options))
        return None if choice == 0 else options[choice - 1][0]

    def table(self, headers, rows):
        if not rows:
            self.say("  (nothing to show)")
            return
        cells = [[str(c) for c in headers]] + [[str(c) for c in row] for row in rows]
        widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
        line = lambda r: "  " + " | ".join(c.ljust(w) for c, w in zip(r, widths))
        self.say(line(cells[0]))
        self.say("  " + "-+-".join("-" * w for w in widths))
        for r in cells[1:]:
            self.say(line(r))

    def work(self, counter, what):
        parts = []
        if counter.comparisons:
            parts.append(f"{counter.comparisons:,} comparison{'s' if counter.comparisons != 1 else ''}")
        if counter.steps:
            parts.append(f"{counter.steps:,} step{'s' if counter.steps != 1 else ''}")
        self.say(f"  [{what}: {', '.join(parts) or 'no work needed'}]")

    # ------------------------------------------------------------------ shared look-ups
    @property
    def directory(self):
        if self._directory is None:
            self._directory = StudentDirectory.load(include_archived=True)
        return self._directory

    def refresh(self):
        self._directory = None

    def find_student(self, prompt="Student ID number, username or e-mail"):
        ident = self.ask(prompt)
        counter = OpCounter()
        student = self.directory.find(ident, counter)
        self.work(counter, "binary search on the ID list, then hash look-up")
        if student is None:
            self.say("  No student matches that ID number, username or e-mail.")
        return student

    def pick_subject(self):
        subjects = merge_sort(Subject.objects.select_related("teacher"), key=lambda s: s.subject_code.casefold())
        if not subjects:
            self.say("  No subjects yet. Create subjects in the web dashboard first.")
            return None
        self.table(["Code", "Title", "Instructor"],
                   [(s.subject_code, s.title, full_name(s.teacher)) for s in subjects])
        index = HashIndex(subjects, keys=(lambda s: s.subject_code,))   # code -> subject, O(1)
        code = self.ask("Subject code", validate=lambda v: None if v in index else "Unknown subject code.")
        return index.get(code)

    def pick_session(self, subject, only_with_queue=False):
        sessions = merge_sort(ClassSession.objects.filter(subject=subject), key=lambda s: s.created_at, reverse=True)
        if only_with_queue:
            sessions = [s for s in sessions if len(rogue_scans_for_session(s))]
        sessions = sessions[:15]
        if not sessions:
            self.say("  No matching sessions for this subject.")
            return None
        self.table(["No.", "Date", "Started", "Title", "Open"],
                   [(i, s.date, localtime(s.created_at).strftime("%I:%M %p"), s.session_title or "-",
                     "yes" if s.is_open else "no") for i, s in enumerate(sessions, start=1)])
        return sessions[self.ask_int("Session number", 1, len(sessions)) - 1]

    # ------------------------------------------------------------------ main loop
    def run(self):
        self.say("=" * 60)
        self.say(" SmartAttendance – command-line menu")
        self.say(" Data structures: list, dictionary (hash index), FIFO queue")
        self.say(" Algorithms: merge sort, binary search, linear search, counting")
        self.say("=" * 60)
        menu = [
            ("students", "Students (add, view, search, update, archive)"),
            ("subjects", "Subjects and enrollment"),
            ("attendance", "Attendance records"),
            ("reports", "Reports and rankings"),
            ("queue", "Unenrolled-scan queue (first come, first served)"),
            ("demo", "Algorithm demonstration"),
        ]
        try:
            while True:
                choice = self.choose("MAIN MENU", menu)
                if choice is None:
                    break
                getattr(self, f"menu_{choice}")()
        except (Quit, KeyboardInterrupt):
            self.say()
        self.say("Goodbye.")

    def submenu(self, title, actions):
        while True:
            choice = self.choose(title, [(fn, label) for fn, label in actions])
            if choice is None:
                return
            try:
                choice()
            except (Quit, KeyboardInterrupt):
                raise
            except (ValueError, ValidationError, IntegrityError, DatabaseError) as exc:
                # Never crash during normal use: report the problem and return to the menu.
                message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
                self.say(f"  Could not complete that: {message}")

    # ------------------------------------------------------------------ students
    def menu_students(self):
        self.submenu("STUDENTS", [
            (self.list_students, "List students (sort by name, ID or attendance rate)"),
            (self.search_students, "Search students"),
            (self.add_student, "Add a student"),
            (self.update_student, "Update a student"),
            (self.archive_student, "Archive or restore a student (soft delete)"),
            (self.unbind_device, "Unbind a student's phone"),
        ])

    def list_students(self):
        sort = self.choose("Sort students by", [("name", "Name (A–Z)"), ("id", "Student ID"),
                                                ("rate", "Attendance rate (highest first)")]) or "name"
        rows = student_rows_with_standing(self.directory.students)
        rank_by_attendance(rows)
        counter = OpCounter()
        rows = sort_roster(rows, sort, counter)
        self.table(["Rank", "ID number", "Name", "Program / Year", "Account", "P", "L", "A", "Rate", "Standing"], [
            (r.rank, r.id_number or "-", full_name(r), self._program(r),
             r.status if r.is_active else "archived", r.n_present, r.n_late, r.n_absent,
             f"{r.attendance_rate:.0f}%", r.standing) for r in rows])
        self.work(counter, f"merge sort of {len(rows)} students")

    @staticmethod
    def _program(s):
        profile = getattr(s, "student_profile", None)
        return f"{profile.course_program} – {profile.year_level}" if profile else "-"

    def search_students(self):
        text = self.ask("Search (full ID number, or part of a name, username or e-mail)")
        exact, partial = OpCounter(), OpCounter()
        by_id = self.directory.find_by_id_number(text, exact)
        self.work(exact, f"binary search for ID '{text}' among {len(self.directory.by_id_number)} IDs")
        matches = [by_id] if by_id else self.directory.search(text, partial)
        if not by_id:
            self.work(partial, f"linear search of {len(self.directory)} students")
        self.table(["ID number", "Name", "Username", "E-mail", "Account", "Phone"], [
            (s.id_number or "-", full_name(s), s.username, s.email, s.status if s.is_active else "archived",
             "bound" if s.device_id else "-") for s in matches])

    def add_student(self):
        self.say("Enter the new student's details (same rules as the web sign-up form).")
        data = {
            "role": User.Role.STUDENT,
            "username": self.ask("Username"),
            "first_name": self.ask("First name"),
            "last_name": self.ask("Last name"),
            "email": self.ask("E-mail", validate=self._email_error),
            "student_id_number": self.ask("Student ID number (e.g. 2024-0012)", max_length=50),
            "course_program": self.ask("Course / program", default="BS Information Systems"),
            "year_level": str(self.ask_int("Year level", 1, 5, default=1)),
            "city_municipality": self.ask("City / municipality", max_length=100),
            "province": self.ask("Province", max_length=100),
            "barangay": self.ask("Barangay", required=False, max_length=100),
        }
        data["password1"] = self.ask_secret("Password (at least 8 characters)")
        data["password2"] = self.ask_secret("Repeat the password")
        form = RegistrationForm(data)
        if not form.is_valid():
            for field, errors in form.errors.items():
                for e in errors:
                    self.say(f"  {field.replace('_', ' ')}: {e}")
            self.say("  The student was not added.")
            return
        user = form.save()
        self.refresh()
        self.say(f"  Added {full_name(user)} ({user.id_number}). The account stays pending until the student is enrolled.")

    @staticmethod
    def _email_error(value):
        try:
            validate_email(value)
        except ValidationError:
            return "Enter a valid e-mail address."
        return None

    def update_student(self):
        s = self.find_student()
        if s is None:
            return
        profile = getattr(s, "student_profile", None)
        self.say(f"  Editing {full_name(s)}. Press Enter to keep the value in brackets.")
        first = self.ask("First name", default=s.first_name)
        last = self.ask("Last name", default=s.last_name)

        def email_check(v):
            if self._email_error(v):
                return self._email_error(v)
            if User.objects.filter(email__iexact=v).exclude(pk=s.pk).exists():
                return "Another account already uses this e-mail."
            return None
        email = self.ask("E-mail", default=s.email, validate=email_check)
        course = self.ask("Course / program", default=profile.course_program if profile else "BS Information Systems")
        year = self.ask_int("Year level", 1, 5, default=profile.year_level if profile else 1)
        status = self.ask("Account status (pending/active/suspended)", default=s.status,
                          validate=lambda v: None if v in User.AccountStatus.values else "Type pending, active or suspended.")
        with transaction.atomic():
            s.first_name, s.last_name, s.email, s.status = first, last, email.lower(), status
            s.save(update_fields=["first_name", "last_name", "email", "status"])
            if profile:
                profile.course_program, profile.year_level = course, year
                profile.save(update_fields=["course_program", "year_level"])
        self.refresh()
        self.say(f"  Saved changes to {full_name(s)}.")

    def archive_student(self):
        s = self.find_student()
        if s is None:
            return
        verb = "Restore" if not s.is_active else "Archive"
        if self.confirm(f"{verb} {full_name(s)}? Attendance history is always kept"):
            s.is_active = not s.is_active
            s.save(update_fields=["is_active"])
            self.refresh()
            self.say(f"  {full_name(s)} is now {'active' if s.is_active else 'archived'}.")

    def unbind_device(self):
        s = self.find_student()
        if s is None:
            return
        if not s.device_id:
            self.say("  This student has no phone bound.")
        elif self.confirm(f"Unbind the phone of {full_name(s)}? Their next scan will bind a new phone"):
            s.device_id = None
            s.save(update_fields=["device_id"])
            self.say("  Phone unbound.")

    # ------------------------------------------------------------------ subjects & enrollment
    def menu_subjects(self):
        self.submenu("SUBJECTS AND ENROLLMENT", [
            (self.list_subjects, "List subjects"),
            (self.view_roster, "View a class roster (sorted and ranked)"),
            (self.enroll, "Enroll students by ID (one or many)"),
            (self.drop, "Drop a student from a subject"),
        ])

    def list_subjects(self):
        subjects = merge_sort(Subject.objects.select_related("teacher", "department"),
                              key=lambda s: s.subject_code.casefold())
        enrolled = count_by(SubjectEnrollment.objects.values_list("subject_id", flat=True))
        held = count_by(ClassSession.objects.values_list("subject_id", flat=True))
        self.table(["Code", "Title", "Instructor", "Department", "Students", "Sessions"], [
            (s.subject_code, s.title, full_name(s.teacher), s.department.code if s.department else "-",
             enrolled.get(s.pk, 0), held.get(s.pk, 0)) for s in subjects])

    def view_roster(self):
        subject = self.pick_subject()
        if subject is None:
            return
        options = [(k, label) for k, (label, _) in ROSTER_SORTS.items()]
        sort = self.choose("Sort the roster by", options) or "name"
        students = User.objects.filter(enrolled_subjects__subject=subject).select_related("student_profile")
        rows = student_rows_with_standing(students, AttendanceLog.objects.filter(session__subject=subject))
        rank_by_attendance(rows)
        counter = OpCounter()
        rows = sort_roster(rows, sort, counter)
        self.table(["Rank", "ID number", "Name", "P", "L", "A", "Rate", "Standing"], [
            (r.rank, r.id_number or "-", full_name(r), r.n_present, r.n_late, r.n_absent,
             f"{r.attendance_rate:.0f}%", r.standing) for r in rows])
        self.work(counter, f"merge sort of {len(rows)} students")

    def enroll(self):
        subject = self.pick_subject()
        if subject is None:
            return
        ids = split_identifiers(self.ask("Student IDs, usernames or e-mails (separate with commas or spaces)",
                                         max_length=20000), limit=200)
        result = enroll_students(subject, ids, StudentDirectory.load())
        self.refresh()
        self.say(f"  Enrolled: {', '.join(full_name(s) for s in result['enrolled']) or 'none'}")
        if result["already"]:
            self.say(f"  Already enrolled: {', '.join(full_name(s) for s in result['already'])}")
        if result["suspended"]:
            self.say(f"  Suspended (not enrolled): {', '.join(full_name(s) for s in result['suspended'])}")
        if result["not_found"]:
            self.say(f"  Not found: {', '.join(result['not_found'])}")

    def drop(self):
        subject = self.pick_subject()
        if subject is None:
            return
        s = self.find_student()
        if s is None:
            return
        enrollment = SubjectEnrollment.objects.filter(subject=subject, student=s).first()
        if enrollment is None:
            self.say(f"  {full_name(s)} is not enrolled in {subject.subject_code}.")
        elif self.confirm(f"Drop {full_name(s)} from {subject.subject_code}? The account and history are kept"):
            enrollment.delete()
            self.say("  Dropped.")

    # ------------------------------------------------------------------ attendance
    def menu_attendance(self):
        self.submenu("ATTENDANCE RECORDS", [
            (self.list_sessions, "List sessions of a subject with counts"),
            (self.view_session, "View the attendance of one session"),
            (self.record_attendance, "Record or update a student's attendance"),
        ])

    def list_sessions(self):
        subject = self.pick_subject()
        if subject is None:
            return
        sessions = merge_sort(ClassSession.objects.filter(subject=subject), key=lambda s: s.created_at, reverse=True)
        counter = OpCounter()
        tallies = count_by_group(AttendanceLog.objects.filter(session__subject=subject)
                                 .values_list("session_id", "status"), initial=STATUSES, counter=counter)
        empty = {k: 0 for k in STATUSES}
        self.table(["Date", "Started", "Title", "Present", "Late", "Absent", "Open"], [
            (s.date, localtime(s.created_at).strftime("%I:%M %p"), s.session_title or "-",
             tallies.get(s.pk, empty)["present"], tallies.get(s.pk, empty)["late"], tallies.get(s.pk, empty)["absent"],
             "yes" if s.is_open else "no") for s in sessions])
        self.work(counter, "one-pass counting of attendance records")

    def view_session(self):
        subject = self.pick_subject()
        session = subject and self.pick_session(subject)
        if not session:
            return
        logs = {log.student_id: log for log in session.attendance_logs.all()}       # dictionary: student -> log
        students = merge_sort(User.objects.filter(enrolled_subjects__subject=subject).select_related("student_profile"),
                              key=lambda s: (s.last_name.casefold(), s.first_name.casefold()))
        self.table(["ID number", "Name", "Status", "Scanned at", "Notes"], [
            (s.id_number or "-", full_name(s), logs[s.pk].status if s.pk in logs else "not scanned",
             localtime(logs[s.pk].scanned_at).strftime("%I:%M %p") if s.pk in logs and logs[s.pk].scanned_device_id else "-",
             (logs[s.pk].notes[:40] if s.pk in logs else "")) for s in students])
        counts = count_by((log.status for log in logs.values()), initial=STATUSES)
        self.say(f"  Present {counts['present']} · Late {counts['late']} · Absent {counts['absent']} · "
                 f"Not scanned {len(students) - len(logs)}")

    def record_attendance(self):
        subject = self.pick_subject()
        session = subject and self.pick_session(subject)
        if not session:
            return
        s = self.find_student()
        if s is None:
            return
        code = self.ask("Status: p = present, l = late, a = absent",
                        validate=lambda v: None if v.lower() in STATUS_CHOICES else "Type p, l or a.")
        notes = self.ask("Notes (optional)", required=False, max_length=500)
        log, created = set_attendance(session, s.pk, status=STATUS_CHOICES[code.lower()], notes=notes or None)
        self.say(f"  {'Recorded' if created else 'Updated'}: {full_name(s)} is {log.status} for this session.")

    # ------------------------------------------------------------------ reports
    def menu_reports(self):
        self.submenu("REPORTS AND RANKINGS", [
            (self.report_overall, "Attendance summary for the whole school"),
            (self.report_subjects, "Attendance summary per subject"),
            (self.report_ranking, "Class ranking by attendance rate"),
            (self.report_watchlist, "Watch list (students not in good standing)"),
        ])

    def report_overall(self):
        counter = OpCounter()
        counts = count_by(AttendanceLog.objects.values_list("status", flat=True), initial=STATUSES, counter=counter)
        s = summary_from_counts(counts)
        self.table(["Records", "Present", "Late", "Absent", "Attendance rate", "On time"],
                   [(s["total"], s["present"], s["late"], s["absent"], f"{s['attendance_rate']}%", f"{s['on_time_rate']}%")])
        self.work(counter, "one-pass counting")

    def report_subjects(self):
        subjects = merge_sort(Subject.objects.all(), key=lambda s: s.subject_code.casefold())
        counter = OpCounter()
        tallies = count_by_group(AttendanceLog.objects.values_list("session__subject_id", "status"),
                                 initial=STATUSES, counter=counter)
        rows = []
        for subj in subjects:
            s = summary_from_counts(tallies.get(subj.pk, {}))
            rows.append((subj.subject_code, s["present"], s["late"], s["absent"], f"{s['attendance_rate']}%"))
        self.table(["Subject", "Present", "Late", "Absent", "Attendance rate"], rows)
        self.work(counter, "one-pass counting per subject")

    def report_ranking(self):
        subject = self.pick_subject()
        if subject is None:
            return
        students = User.objects.filter(enrolled_subjects__subject=subject).select_related("student_profile")
        rows = rank_by_attendance(student_rows_with_standing(students, AttendanceLog.objects.filter(session__subject=subject)))
        self.table(["Rank", "Name", "ID number", "Rate", "Late", "Absent", "Standing"], [
            (r.rank, full_name(r), r.id_number or "-", f"{r.attendance_rate:.1f}%", r.n_late, r.n_absent, r.standing)
            for r in rows])
        self.say("  Ranked with merge sort; equal attendance rates share a rank (1, 2, 2, 4).")

    def report_watchlist(self):
        students = User.objects.filter(role=User.Role.STUDENT, is_active=True).select_related("student_profile")
        rows = watchlist(student_rows_with_standing(students))
        self.table(["Standing", "Name", "ID number", "Rate", "Late", "Absent"], [
            (r.standing, full_name(r), r.id_number or "-", f"{r.attendance_rate:.1f}%", r.n_late, r.n_absent)
            for r in rows])

    # ------------------------------------------------------------------ queue
    def menu_queue(self):
        self.submenu("UNENROLLED-SCAN QUEUE", [
            (self.show_queue, "Show the queue for a session"),
            (self.approve_next, "Approve the next student in the queue"),
        ])

    def _queue_for(self):
        subject = self.pick_subject()
        session = subject and self.pick_session(subject, only_with_queue=True)
        return (session, rogue_scans_for_session(session)) if session else (None, None)

    def show_queue(self):
        session, queue = self._queue_for()
        if session is None:
            return
        self.table(["Position", "Name", "ID number", "First scan"], [
            (i, full_name(a.student), a.student.id_number or "-", localtime(a.created_at).strftime("%I:%M:%S %p"))
            for i, a in enumerate(queue, start=1)])

    def approve_next(self):
        session, queue = self._queue_for()
        if session is None:
            return
        try:
            audit = queue.peek()
        except QueueEmpty:
            self.say("  The queue is empty.")
            return
        if self.confirm(f"Add {full_name(audit.student)} to {session.subject.subject_code} and record attendance"):
            queue.dequeue()
            log, _ = approve_rogue_scan(session, audit)
            self.refresh()
            self.say(f"  {full_name(audit.student)} enrolled and marked {log.status}. {len(queue)} left in the queue.")

    # ------------------------------------------------------------------ algorithm demo
    def menu_demo(self):
        self.submenu("ALGORITHM DEMONSTRATION", [
            (self.demo_search, "Linear search vs binary search on real student IDs"),
            (self.demo_sort, "Merge sort: comparisons vs n·log2 n"),
            (self.demo_benchmark, "Benchmark on 1,000 / 10,000 / 100,000 generated IDs"),
        ])

    def demo_search(self):
        ids = [s.id_number for s in self.directory.students if s.id_number]
        if not ids:
            self.say("  There are no student ID numbers to search yet.")
            return
        target = self.ask(f"ID number to find (e.g. {ids[0]})")
        lin = OpCounter()
        found_at = benchmark.linear_find([normalize_key(i) for i in ids], normalize_key(target), lin)
        sorted_ids = merge_sort(ids, key=normalize_key)
        binc = OpCounter()
        pos = binary_search(sorted_ids, normalize_key(target), key=normalize_key, counter=binc)
        n = len(ids)
        self.table(["Algorithm", "List", "Found", "Comparisons", "Worst case"], [
            ("Linear search", f"{n} unsorted", "yes" if found_at >= 0 else "no", lin.comparisons, n),
            ("Binary search", f"{n} sorted", "yes" if pos >= 0 else "no", binc.comparisons, math.floor(math.log2(n)) + 1),
        ])

    def demo_sort(self):
        rows = student_rows_with_standing(self.directory.students)
        n = len(rows)
        counter = OpCounter()
        ordered = merge_sort(rows, key=lambda r: (-r.attendance_rate, full_name(r).casefold()), counter=counter)
        self.say(f"  Sorted {n} students by attendance rate (highest first).")
        self.say(f"  Comparisons used: {counter.comparisons:,}; upper bound n·log2 n ≈ {round(n * math.log2(n)) if n > 1 else 0:,}")
        self.table(["#", "Name", "Rate"], [(i, full_name(r), f"{r.attendance_rate:.1f}%")
                                           for i, r in enumerate(ordered[:10], start=1)])

    def demo_benchmark(self):
        self.say("  Running … (generated data only; the database is not touched)")
        self.say(benchmark.format_table(benchmark.run(sizes=(1_000, 10_000, 100_000), lookups=100)))
