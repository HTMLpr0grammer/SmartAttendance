"""
In-memory student directory: the data structures behind student look-ups.

The directory is loaded from the database once per request (or once per command-line session) and is
never written back, so it adds no tables and no columns.

    by_id_number   list of students sorted with merge sort by ID number   -> binary search, O(log n)
    by_login       hash index (dictionary) of usernames and e-mails       -> O(1) average
    students       the plain list                                         -> linear search on names, O(n)
"""
import re

from .dsa import HashIndex, binary_search, linear_search, merge_sort, normalize_key
from .models import User

# Separators accepted when a class list of student IDs is pasted in one go.
LIST_SEPARATORS = re.compile(r"[\s,;]+")


def split_identifiers(text, limit=None):
    """Split pasted text into unique identifiers, keeping their order (a set remembers what we've seen)."""
    seen, result = set(), []
    for token in LIST_SEPARATORS.split(text or ""):
        k = normalize_key(token)
        if k and k not in seen:
            seen.add(k)
            result.append(token.strip())
    return result[:limit] if limit else result


def full_name(student):
    return student.get_full_name() or student.username


class StudentDirectory:
    def __init__(self, students):
        self.students = list(students)
        # Sorted once with merge sort so every ID look-up afterwards can use binary search.
        self.by_id_number = merge_sort(
            [s for s in self.students if s.id_number], key=lambda s: normalize_key(s.id_number)
        )
        self.by_login = HashIndex(self.students, keys=(lambda s: s.username, lambda s: s.email))

    @classmethod
    def load(cls, include_archived=False):
        qs = User.objects.filter(role=User.Role.STUDENT).select_related("student_profile")
        if not include_archived:
            qs = qs.filter(is_active=True)
        return cls(qs)

    def __len__(self):
        return len(self.students)

    # -- look-ups -----------------------------------------------------------
    def find_by_id_number(self, id_number, counter=None):
        """Binary search on the list sorted by ID number."""
        index = binary_search(self.by_id_number, normalize_key(id_number),
                              key=lambda s: normalize_key(s.id_number), counter=counter)
        return self.by_id_number[index] if index >= 0 else None

    def find(self, identifier, counter=None):
        """Find one student by ID number, username or e-mail (exact match, case-insensitive)."""
        return self.find_by_id_number(identifier, counter) or self.by_login.get(identifier)

    def search(self, text, counter=None):
        """Partial match on name, ID number, username or e-mail (linear search)."""
        return linear_search(
            self.students, text,
            fields=(full_name, lambda s: s.id_number, lambda s: s.username, lambda s: s.email),
            counter=counter,
        )
