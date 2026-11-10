from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import SubjectEnrollment, User


@receiver(post_save, sender=SubjectEnrollment)
def promote_pending_student(sender, instance, created, **kwargs):
    """
    Automatic Student Promotion: the moment any instructor enrolls a pending
    student into a subject, the student becomes ACTIVE. Suspended accounts are
    never auto-reactivated.
    """
    if not created:
        return
    User.objects.filter(
        pk=instance.student_id,
        role=User.Role.STUDENT,
        status=User.AccountStatus.PENDING,
    ).update(status=User.AccountStatus.ACTIVE)
