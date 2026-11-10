"""
Data structures and algorithms used by SmartAttendance.

This module is plain Python (no Django) so each algorithm can be read, tested and explained on its own.
The rest of the application loads rows from the database into Python lists and dictionaries and then
uses the functions below to search, sort, rank, count and queue them.

Nothing here writes to the database. Counts, ranks and indexes are *derived* values that are rebuilt
whenever they are needed, so the database stays in third normal form (3NF).

Data structures
    HashIndex         dictionary (hash map) for average O(1) look-ups by a key
    Queue             FIFO queue (first in, first out), O(1) enqueue and dequeue

Algorithms
    merge_sort        stable sorting, O(n log n), written by hand
    binary_search     search in a sorted list, O(log n)
    linear_search     substring search in any list, O(n)
    count_by          single-pass counting with a dictionary, O(n)
    count_by_group    single-pass counting per group (e.g. per student), O(n)
    competition_rank  1, 2, 2, 4 ranking of an already sorted list, O(n)

Every algorithm accepts an optional OpCounter so the command-line menu and the benchmark can show how
much work was done (comparisons and steps).
"""
from collections import deque


# ---------------------------------------------------------------------------
# Instrumentation
# ---------------------------------------------------------------------------
class OpCounter:
    """Counts the basic operations an algorithm performs.

    comparisons – key comparisons (the usual measure for searching and sorting)
    steps       – other units of work, e.g. items visited by a counting loop
    """

    def __init__(self):
        self.comparisons = 0
        self.steps = 0

    def __repr__(self):
        return f"OpCounter(comparisons={self.comparisons}, steps={self.steps})"


def _identity(value):
    return value


def normalize_key(value):
    """Case- and space-insensitive form of a text key (used by searches and the hash index)."""
    return str(value or "").strip().casefold()


# ---------------------------------------------------------------------------
# Sorting: merge sort
# ---------------------------------------------------------------------------
def merge_sort(items, key=None, reverse=False, counter=None):
    """Return a new list with *items* sorted by *key*.

    Merge sort splits the list in half, sorts each half recursively and then merges the two sorted
    halves by repeatedly taking the smaller front element.

    * Time:  O(n log n) in the best, average and worst case.
    * Space: O(n) for the merged lists.
    * Stable: items with equal keys keep their original order, which lets us sort by one field and then
      by another without losing the first order. Tuple keys such as (-rate, last_name) sort by several
      fields at once.
    """
    key = key or _identity
    # Compute each key once ("decorate"), sort the (key, item) pairs, then drop the keys.
    decorated = [(key(item), item) for item in items]
    return [item for _, item in _merge_sort(decorated, reverse, counter)]


def _merge_sort(pairs, reverse, counter):
    if len(pairs) <= 1:                     # a list of 0 or 1 items is already sorted
        return pairs
    middle = len(pairs) // 2
    left = _merge_sort(pairs[:middle], reverse, counter)
    right = _merge_sort(pairs[middle:], reverse, counter)
    return _merge(left, right, reverse, counter)


def _merge(left, right, reverse, counter):
    merged = []
    i = j = 0
    while i < len(left) and j < len(right):
        if counter is not None:
            counter.comparisons += 1
        left_key, right_key = left[i][0], right[j][0]
        # Taking from the left half when keys are equal is what keeps the sort stable.
        take_left = left_key >= right_key if reverse else left_key <= right_key
        if take_left:
            merged.append(left[i])
            i += 1
        else:
            merged.append(right[j])
            j += 1
    merged.extend(left[i:])                 # one of these two is empty
    merged.extend(right[j:])
    return merged


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------
def binary_search(sorted_items, target, key=None, counter=None):
    """Return the index of the item whose key equals *target*, or -1 if there is none.

    *sorted_items* must already be sorted by the same key (for example with merge_sort). Each step
    compares the target with the middle item and discards the half that cannot contain it.

    * Time: O(log n) – 1,000 items need at most 10 comparisons, 1,000,000 items at most 20.
    """
    key = key or _identity
    low, high = 0, len(sorted_items) - 1
    while low <= high:
        middle = (low + high) // 2
        if counter is not None:
            counter.comparisons += 1
        middle_key = key(sorted_items[middle])
        if middle_key == target:
            return middle
        if middle_key < target:
            low = middle + 1                # target can only be in the right half
        else:
            high = middle - 1               # target can only be in the left half
    return -1


def linear_search(items, text, fields, counter=None):
    """Return every item where *text* appears inside any of the *fields* (case-insensitive).

    Linear search checks the items one by one, so it works on unsorted data and can match part of a
    name (e.g. "ram" finds "Ramirez"), which binary search cannot do.

    * Time: O(n × f) for n items and f fields.
    """
    needle = normalize_key(text)
    if not needle:
        return list(items)
    matches = []
    for item in items:
        for field in fields:
            if counter is not None:
                counter.comparisons += 1
            if needle in normalize_key(field(item)):
                matches.append(item)
                break                       # one matching field is enough
    return matches


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------
def count_by(items, key=None, initial=(), counter=None):
    """Count how many items fall under each key in one pass, using a dictionary.

    count_by(["present", "late", "present"]) -> {"present": 2, "late": 1}
    Keys listed in *initial* start at 0 so they appear even when nothing matches.

    * Time: O(n). Each dictionary update is O(1) on average.
    """
    key = key or _identity
    counts = {k: 0 for k in initial}
    for item in items:
        if counter is not None:
            counter.steps += 1
        k = key(item)
        counts[k] = counts.get(k, 0) + 1
    return counts


def count_by_group(pairs, initial=(), counter=None):
    """Count (group, value) pairs per group in one pass: a dictionary of dictionaries.

    count_by_group([(7, "present"), (7, "late"), (9, "present")])
        -> {7: {"present": 1, "late": 1}, 9: {"present": 1}}

    * Time: O(n).
    """
    groups = {}
    for group, value in pairs:
        if counter is not None:
            counter.steps += 1
        tally = groups.get(group)
        if tally is None:
            tally = groups[group] = {k: 0 for k in initial}
        tally[value] = tally.get(value, 0) + 1
    return groups


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def competition_rank(sorted_items, key):
    """Give 1-based ranks to an already sorted list; equal keys share a rank ("1, 2, 2, 4").

    Returns a list of (rank, item) pairs in the same order. Time: O(n).
    """
    ranked = []
    previous = object()
    rank = 0
    for position, item in enumerate(sorted_items, start=1):
        value = key(item)
        if value != previous:
            rank = position
            previous = value
        ranked.append((rank, item))
    return ranked


# ---------------------------------------------------------------------------
# Data structure: hash index (dictionary)
# ---------------------------------------------------------------------------
class HashIndex:
    """Dictionary-based index that maps a normalized key to an item.

    Building the index visits each item once (O(n)); afterwards every look-up is O(1) on average,
    because Python dictionaries are hash tables. If two items produce the same key, the first one
    is kept and the key is recorded in *duplicates*.
    """

    def __init__(self, items=(), keys=None):
        self._table = {}
        self.duplicates = set()
        keys = keys or (_identity,)
        for item in items:
            for key in keys:
                self.add(key(item), item)

    def add(self, raw_key, item):
        k = normalize_key(raw_key)
        if not k:
            return
        if k in self._table and self._table[k] is not item:
            self.duplicates.add(k)
            return
        self._table[k] = item

    def get(self, raw_key, default=None):
        return self._table.get(normalize_key(raw_key), default)

    def __contains__(self, raw_key):
        return normalize_key(raw_key) in self._table

    def __len__(self):
        return len(self._table)


# ---------------------------------------------------------------------------
# Data structure: FIFO queue
# ---------------------------------------------------------------------------
class QueueEmpty(Exception):
    """Raised when dequeuing from an empty queue."""


class Queue:
    """First in, first out queue.

    Items leave in the order they arrived – the first student who scanned is the first one the
    instructor sees. Backed by collections.deque, so enqueue (at the back) and dequeue (from the front)
    are both O(1); a plain list would need O(n) to remove its first element.
    """

    def __init__(self, items=()):
        self._items = deque(items)

    def enqueue(self, item):
        self._items.append(item)

    def dequeue(self):
        if not self._items:
            raise QueueEmpty("The queue is empty.")
        return self._items.popleft()

    def peek(self):
        if not self._items:
            raise QueueEmpty("The queue is empty.")
        return self._items[0]

    def is_empty(self):
        return not self._items

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        """Iterate from front (next to be served) to back without removing anything."""
        return iter(tuple(self._items))

    def __bool__(self):
        return bool(self._items)
