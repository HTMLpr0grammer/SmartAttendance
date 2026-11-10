"""
Tests for the data structures and algorithms (attendance/dsa.py) and for the features that use them:
counting-based analytics, roster sorting/search/ranking, bulk enrollment, the unenrolled-scan queue and
the command-line menu.
"""
import io
import random
from datetime import timedelta

from django.core.management import call_command
from django.db.models import Count, Q
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .benchmark import run as run_benchmark
from .cli import AttendanceCLI
from .directory import StudentDirectory, split_identifiers
from .dsa import (
    HashIndex, OpCounter, Queue, QueueEmpty, binary_search, competition_rank, count_by, count_by_group,
    linear_search, merge_sort,
)
from .models import AttendanceLog, AuditScanLog, ClassSession, Subject, SubjectEnrollment, User
from .services import (
    punctuality_summary, rank_by_attendance, rogue_scans_for_session, sort_roster, student_rows_with_standing,
    watchlist,
)


# ---------------------------------------------------------------------------
# Pure algorithms
# ---------------------------------------------------------------------------
class MergeSortTests(SimpleTestCase):
    def test_matches_builtin_sort_on_random_data(self):
        rng = random.Random(7)
        for n in (0, 1, 2, 3, 10, 101, 1000):
            data = [rng.randint(-50, 50) for _ in range(n)]
            self.assertEqual(merge_sort(data), sorted(data))
            self.assertEqual(merge_sort(data, reverse=True), sorted(data, reverse=True))

    def test_is_stable_and_does_not_modify_input(self):
        rows = [("b", 1), ("a", 2), ("b", 0), ("a", 1)]
        original = list(rows)
        self.assertEqual(merge_sort(rows, key=lambda r: r[0]), [("a", 2), ("a", 1), ("b", 1), ("b", 0)])
        self.assertEqual(merge_sort(rows, key=lambda r: r[0], reverse=True), [("b", 1), ("b", 0), ("a", 2), ("a", 1)])
        self.assertEqual(rows, original)

    def test_tuple_keys_sort_by_several_fields(self):
        people = [("Cruz", 80.0), ("Abad", 95.5), ("Bato", 80.0)]
        ordered = merge_sort(people, key=lambda p: (-p[1], p[0]))
        self.assertEqual([p[0] for p in ordered], ["Abad", "Bato", "Cruz"])

    def test_comparisons_stay_within_n_log_n(self):
        import math
        counter = OpCounter()
        data = list(range(1024))
        random.Random(1).shuffle(data)
        merge_sort(data, counter=counter)
        self.assertLessEqual(counter.comparisons, 1024 * math.log2(1024))


class SearchTests(SimpleTestCase):
    def test_binary_search_finds_every_item_and_misses(self):
        items = sorted(random.Random(3).sample(range(10_000), 500))
        for i, value in enumerate(items):
            self.assertEqual(binary_search(items, value), i)
        self.assertEqual(binary_search(items, -1), -1)
        self.assertEqual(binary_search(items, 10_001), -1)
        self.assertEqual(binary_search([], 5), -1)

    def test_binary_search_uses_at_most_log2_n_plus_one_comparisons(self):
        items = list(range(1_000_000))
        counter = OpCounter()
        binary_search(items, 999_999, counter=counter)
        self.assertLessEqual(counter.comparisons, 20)

    def test_linear_search_partial_case_insensitive(self):
        names = ["Ana Santos", "Ben Ramirez", "Carla Mendoza"]
        self.assertEqual(linear_search(names, "RAM", fields=(str,)), ["Ben Ramirez"])
        self.assertEqual(linear_search(names, "a", fields=(str,)), names)
        self.assertEqual(linear_search(names, "zzz", fields=(str,)), [])
        self.assertEqual(linear_search(names, "  ", fields=(str,)), names)  # blank search shows everything


class CountingRankingTests(SimpleTestCase):
    def test_count_by(self):
        counts = count_by(["present", "late", "present"], initial=("present", "late", "absent"))
        self.assertEqual(counts, {"present": 2, "late": 1, "absent": 0})

    def test_count_by_group(self):
        groups = count_by_group([(7, "present"), (7, "late"), (9, "present")], initial=("present", "late"))
        self.assertEqual(groups, {7: {"present": 1, "late": 1}, 9: {"present": 1, "late": 0}})

    def test_competition_rank_shares_ties(self):
        rates = [100, 90, 90, 75]
        self.assertEqual([r for r, _ in competition_rank(rates, key=lambda x: x)], [1, 2, 2, 4])


class StructureTests(SimpleTestCase):
    def test_queue_is_first_in_first_out(self):
        q = Queue()
        for name in ("Ana", "Ben", "Carla"):
            q.enqueue(name)
        self.assertEqual(list(q), ["Ana", "Ben", "Carla"])
        self.assertEqual((q.peek(), q.dequeue(), q.dequeue(), len(q)), ("Ana", "Ana", "Ben", 1))
        q.dequeue()
        self.assertTrue(q.is_empty())
        with self.assertRaises(QueueEmpty):
            q.dequeue()

    def test_hash_index_is_case_insensitive_and_reports_duplicates(self):
        index = HashIndex(["Ana", "ana", "Ben"])
        self.assertEqual(index.get(" ANA "), "Ana")
        self.assertIn("ben", index)
        self.assertIn("ana", index.duplicates)
        self.assertIsNone(index.get("Carla"))

    def test_split_identifiers(self):
        self.assertEqual(split_identifiers("2024-0001, 2024-0002\n2024-0001;  ana"), ["2024-0001", "2024-0002", "ana"])

    def test_benchmark_runs(self):
        row = run_benchmark(sizes=(500,), lookups=20)[0]
        self.assertLess(row["binary_avg_comparisons"], row["linear_avg_comparisons"])
        self.assertLessEqual(row["sort_comparisons"], row["n_log2_n"])


# ---------------------------------------------------------------------------
# Features that use the algorithms (seeded demo data)
# ---------------------------------------------------------------------------
class SeededCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_data", stdout=io.StringIO())
        cls.teacher = User.objects.get(username="jdelacruz")
        cls.subject = Subject.objects.get(subject_code="IS201")


class AnalyticsTests(SeededCase):
    def test_counting_matches_database_aggregate(self):
        logs = AttendanceLog.objects.filter(session__subject=self.subject)
        db = logs.aggregate(p=Count("id", filter=Q(status="present")), l=Count("id", filter=Q(status="late")),
                            a=Count("id", filter=Q(status="absent")))
        s = punctuality_summary(logs)
        self.assertEqual((s["present"], s["late"], s["absent"]), (db["p"], db["l"], db["a"]))

    def test_per_student_counts_match_database(self):
        students = User.objects.filter(role="student")
        rows = {r.pk: r for r in student_rows_with_standing(students)}
        for st in students.annotate(n=Count("attendance_records")):
            self.assertEqual(rows[st.pk].n_total, st.n)

    def test_watchlist_puts_critical_first_then_lowest_rate(self):
        rows = watchlist(student_rows_with_standing(User.objects.filter(role="student")))
        keys = [({"Critical Absences": 0, "Frequent Tardy": 1}[r.standing], r.attendance_rate) for r in rows]
        self.assertEqual(keys, sorted(keys))

    def test_rank_and_sort_roster(self):
        students = User.objects.filter(enrolled_subjects__subject=self.subject)
        rows = student_rows_with_standing(students, AttendanceLog.objects.filter(session__subject=self.subject))
        ranked = rank_by_attendance(rows)
        self.assertEqual(ranked[0].rank, 1)
        self.assertEqual([r.attendance_rate for r in ranked], sorted((r.attendance_rate for r in ranked), reverse=True))
        by_id = sort_roster(rows, "id")
        self.assertEqual([r.id_number for r in by_id], sorted(r.id_number for r in rows))


class RosterPageTests(SeededCase):
    def setUp(self):
        self.client.force_login(self.teacher)
        self.url = reverse("instructor_subject", args=[self.subject.pk])

    def test_sort_search_and_rank(self):
        r = self.client.get(self.url, {"sort": "rate"})
        self.assertEqual(r.status_code, 200)
        rates = [s.attendance_rate for s in r.context["roster"]]
        self.assertEqual(rates, sorted(rates, reverse=True))
        r = self.client.get(self.url, {"q": "ramir"})
        self.assertEqual([s.username for s in r.context["roster"]], ["bramirez"])
        self.assertContains(r, "1 of 4 students match")

    def test_unknown_sort_falls_back_to_name(self):
        r = self.client.get(self.url, {"sort": "nonsense"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["sort"], "name")


class BulkEnrollTests(SeededCase):
    def test_bulk_enroll_by_id_list(self):
        self.client.force_login(self.teacher)
        cs101 = Subject.objects.get(subject_code="CS101")
        before = set(SubjectEnrollment.objects.filter(subject=cs101).values_list("student__username", flat=True))
        r = self.client.post(reverse("api_enroll", args=[cs101.pk]),
                             {"identifiers": "2024-0001, 2024-0002\negarcia 2099-9999"}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        self.assertIn("2099-9999", data["not_found"])
        after = set(SubjectEnrollment.objects.filter(subject=cs101).values_list("student__username", flat=True))
        self.assertTrue({"asantos", "bramirez", "egarcia"} <= after)
        self.assertEqual(len(data["enrolled"]), len(after - before))
        # enrollment activates the pending student (existing signal)
        self.assertEqual(User.objects.get(username="egarcia").status, "active")

    def test_directory_lookups(self):
        d = StudentDirectory.load()
        counter = OpCounter()
        self.assertEqual(d.find("2024-0003", counter).username, "cmendoza")
        self.assertLessEqual(counter.comparisons, 3)
        self.assertEqual(d.find("CMENDOZA").username, "cmendoza")
        self.assertEqual(d.find("cmendoza@student.school.edu.ph").username, "cmendoza")
        self.assertIsNone(d.find("nobody"))

    def test_bulk_enroll_rejects_empty_and_oversized_lists(self):
        self.client.force_login(self.teacher)
        url = reverse("api_enroll", args=[self.subject.pk])
        self.assertEqual(self.client.post(url, {"identifiers": " , "}, content_type="application/json").status_code, 400)
        many = " ".join(f"x{i}" for i in range(201))
        self.assertEqual(self.client.post(url, {"identifiers": many}, content_type="application/json").status_code, 400)


class QueueFeatureTests(SeededCase):
    def test_unenrolled_scans_form_a_fifo_queue(self):
        session = ClassSession.objects.create(subject=self.subject, expires_at=timezone.now() + timedelta(hours=1))
        egarcia = User.objects.get(username="egarcia")
        outsider = User.objects.create_user("outsider", "outsider@x.ph", "x", role="student")
        SubjectEnrollment.objects.filter(subject=self.subject, student=egarcia).delete()
        t0 = timezone.now()
        for who, seconds in ((outsider, 0), (egarcia, 5), (outsider, 9)):
            a = AuditScanLog.objects.create(session=session, student=who, reason="unenrolled_student")
            AuditScanLog.objects.filter(pk=a.pk).update(created_at=t0 + timedelta(seconds=seconds))
        queue = rogue_scans_for_session(session)
        self.assertEqual([a.student.username for a in queue], ["outsider", "egarcia"])   # each student once, by arrival
        self.client.force_login(self.teacher)
        live = self.client.get(reverse("api_session_live", args=[session.pk])).json()
        self.assertEqual([(r["position"], r["name"]) for r in live["rogue"]][0][0], 1)
        names = [r["name"] for r in live["roster"]]
        self.assertEqual(names, [r["name"] for r in sorted(live["roster"], key=lambda r: r["name"].split()[-1])])


# ---------------------------------------------------------------------------
# Command-line menu
# ---------------------------------------------------------------------------
class CLITests(SeededCase):
    def run_cli(self, *lines):
        out = io.StringIO()
        AttendanceCLI(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out).run()
        return out.getvalue()

    def test_full_record_workflow(self):
        out = self.run_cli(
            "1", "3",                                   # Students -> Add
            "newkid", "New", "Kid", "newkid@school.edu.ph", "2026-0100", "", "", "Aurora", "Zamboanga del Sur", "",
            "Str0ng-Pass!23", "Str0ng-Pass!23",
            "2", "2026-0100",                            # Search (binary search hit)
            "4", "2026-0100", "", "", "", "", "3", "",   # Update -> year level 3
            "0",
            "2", "3", "IS201", "2026-0100, nobody-xyz",  # Enroll (bulk)
            "0",
            "3", "3", "IS201", "1", "2026-0100", "l", "Bus was late",   # Record attendance
            "0",
            "4", "1", "4", "3", "IS201", "0",           # Reports: overall, watch list, ranking
            "1", "5", "2026-0100", "y",                  # Archive (soft delete)
            "0", "0",
        )
        kid = User.objects.get(username="newkid")
        self.assertEqual(kid.student_profile.year_level, 3)
        self.assertFalse(kid.is_active)
        self.assertEqual(kid.status, "active")          # promoted by enrollment
        self.assertTrue(SubjectEnrollment.objects.filter(subject=self.subject, student=kid).exists())
        log = AttendanceLog.objects.get(student=kid)
        self.assertEqual((log.status, log.notes), ("late", "Bus was late"))
        self.assertIn("Not found: nobody-xyz", out)
        self.assertIn("binary search for ID '2026-0100'", out)
        self.assertIn("Ranked with merge sort", out)
        self.assertTrue(out.rstrip().endswith("Goodbye."))

    def test_bad_input_never_crashes(self):
        out = self.run_cli("9", "abc", "", "1", "2", "zzz-nobody", "4", "nobody", "0", "0")
        self.assertIn("Enter a whole number from 0 to 6.", out)
        self.assertIn("No student matches", out)
        self.assertTrue(out.rstrip().endswith("Goodbye."))

    def test_invalid_new_student_is_rejected_with_reasons(self):
        out = self.run_cli("1", "3", "asantos", "Dup", "Licate", "dup@school.edu.ph", "2024-0001", "", "", "X", "Y", "",
                           "short", "different", "0", "0")
        self.assertIn("The student was not added.", out)
        self.assertFalse(User.objects.filter(email="dup@school.edu.ph").exists())

    def test_end_of_input_exits_cleanly(self):
        out = self.run_cli("1")       # input ends while a menu is waiting
        self.assertTrue(out.rstrip().endswith("Goodbye."))

    def test_algorithm_demo(self):
        out = self.run_cli("6", "1", "2024-0002", "2", "0", "0")
        self.assertIn("Binary search", out)
        self.assertIn("upper bound n·log2 n", out)
