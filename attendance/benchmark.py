"""
Measures the searching and sorting algorithms of attendance/dsa.py on synthetic student IDs.

Runs entirely in memory (nothing is read from or written to the database). Used by
`python manage.py dsa_benchmark` and by the "Algorithm demonstration" menu of `attendance_cli`.
"""
import math
import random
import time

from .dsa import OpCounter, binary_search, merge_sort


def synthetic_ids(n, seed=2026):
    """n unique student IDs such as '2026-00042', shuffled so the list starts unsorted."""
    rng = random.Random(seed)
    ids = [f"{2020 + i % 7}-{i:05d}" for i in range(n)]
    rng.shuffle(ids)
    return ids


def linear_find(items, target, counter):
    """Exact-match linear search, counting comparisons (used only for the comparison)."""
    for index, item in enumerate(items):
        counter.comparisons += 1
        if item == target:
            return index
    return -1


def _best_ms(fn, repeat):
    """Run *fn* several times and keep the fastest time, which is least disturbed by other programs."""
    best = None
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        elapsed = (time.perf_counter() - t0) * 1000
        best = elapsed if best is None else min(best, elapsed)
    return best


def run(sizes=(1_000, 10_000, 100_000), lookups=200, seed=2026, repeat=3):
    """Return one result row per list size.

    For each size: merge sort the IDs once, then look up *lookups* random IDs (plus one missing ID)
    with linear search on the unsorted list and with binary search on the sorted list. Comparisons are
    counted exactly; times are the best of *repeat* runs.
    """
    rng = random.Random(seed)
    rows = []
    for n in sizes:
        ids = synthetic_ids(n, seed)
        sort_counter = OpCounter()
        ordered = merge_sort(ids, counter=sort_counter)
        sort_ms = _best_ms(lambda: merge_sort(ids), repeat)
        builtin_ms = _best_ms(lambda: sorted(ids), repeat)

        targets = rng.sample(ids, min(lookups, n)) + ["9999-99999"]  # last one is never found
        lin, bin_ = OpCounter(), OpCounter()
        for t in targets:
            linear_find(ids, t, lin)
            binary_search(ordered, t, counter=bin_)
        linear_ms = _best_ms(lambda: [linear_find(ids, t, OpCounter()) for t in targets], repeat)
        binary_ms = _best_ms(lambda: [binary_search(ordered, t) for t in targets], repeat)

        rows.append({
            "n": n,
            "sort_comparisons": sort_counter.comparisons,
            "n_log2_n": round(n * math.log2(n)),
            "merge_sort_ms": round(sort_ms, 2),
            "builtin_sort_ms": round(builtin_ms, 2),
            "lookups": len(targets),
            "linear_avg_comparisons": round(lin.comparisons / len(targets), 1),
            "binary_avg_comparisons": round(bin_.comparisons / len(targets), 1),
            "binary_max_comparisons": math.floor(math.log2(n)) + 1,
            "linear_ms": round(linear_ms, 2),
            "binary_ms": round(binary_ms, 2),
        })
    return rows


def format_table(rows):
    lines = [
        f"{'Students':>9} | {'Merge sort cmp':>14} | {'n·log2 n':>10} | {'Merge ms':>8} | {'sorted() ms':>11} | "
        f"{'Linear avg cmp':>14} | {'Binary avg cmp':>14} | {'Linear ms':>9} | {'Binary ms':>9}",
    ]
    lines.append("-" * len(lines[0]))
    for r in rows:
        lines.append(
            f"{r['n']:>9,} | {r['sort_comparisons']:>14,} | {r['n_log2_n']:>10,} | {r['merge_sort_ms']:>8} | "
            f"{r['builtin_sort_ms']:>11} | {r['linear_avg_comparisons']:>14,} | {r['binary_avg_comparisons']:>14} | "
            f"{r['linear_ms']:>9} | {r['binary_ms']:>9}"
        )
    return "\n".join(lines)
