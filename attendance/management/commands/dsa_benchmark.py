"""python manage.py dsa_benchmark – compare linear vs binary search and measure merge sort."""
from django.core.management.base import BaseCommand

from attendance.benchmark import format_table, run


class Command(BaseCommand):
    help = "Benchmark the hand-written searching and sorting algorithms on synthetic student IDs (no database use)."

    def add_arguments(self, parser):
        parser.add_argument("--sizes", default="1000,10000,100000",
                            help="Comma-separated list sizes (default: 1000,10000,100000)")
        parser.add_argument("--lookups", type=int, default=200, help="Random look-ups per size (default: 200)")

    def handle(self, *args, **opts):
        try:
            sizes = [int(x) for x in opts["sizes"].split(",") if x.strip()]
        except ValueError:
            self.stderr.write("--sizes must be whole numbers separated by commas, e.g. 1000,10000")
            return
        if not sizes or min(sizes) < 2 or opts["lookups"] < 1:
            self.stderr.write("Sizes must be at least 2 and --lookups at least 1.")
            return
        self.stdout.write("Benchmarking merge sort, linear search and binary search …\n")
        self.stdout.write(format_table(run(sizes=sizes, lookups=opts["lookups"])))
        self.stdout.write(
            "\ncmp = key comparisons. Linear search checks items one by one (about n/2 on average); binary search "
            "halves a sorted list each step (at most ⌊log2 n⌋ + 1).\nMerge sort makes at most n·log2 n comparisons. "
            "Python's built-in sorted() (Timsort, written in C) is shown only for reference."
        )
