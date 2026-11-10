from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.db import transaction

from .models import Address, Department, StudentProfile, Subject, User

INPUT_CSS = (
    "block w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 "
    "shadow-sm placeholder:text-slate-400 focus:border-brand-600 focus:outline-none "
    "focus:ring-2 focus:ring-brand-600/30"
)
CHECK_CSS = "h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-600"


class TailwindMixin:
    def _style_fields(self):
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, (forms.CheckboxInput,)):
                w.attrs.setdefault("class", CHECK_CSS)
            else:
                w.attrs.setdefault("class", INPUT_CSS)
            if field.required and not isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("required", "required")


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
class LoginForm(TailwindMixin, AuthenticationForm):
    username = forms.CharField(
        label="Username or e-mail",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username",
                                      "placeholder": "you@school.edu.ph"}),
    )
    password = forms.CharField(
        label="Password", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "••••••••"}),
    )

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Invalid credentials, or the account has been archived.",
        "suspended": "This account is suspended. Please contact the administrator.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.status == User.AccountStatus.SUSPENDED:
            raise forms.ValidationError(self.error_messages["suspended"], code="suspended")


class RegistrationForm(TailwindMixin, UserCreationForm):
    ROLE_CHOICES = [(User.Role.STUDENT, "Student"), (User.Role.INSTRUCTOR, "Instructor")]

    role = forms.ChoiceField(choices=ROLE_CHOICES, widget=forms.RadioSelect, initial=User.Role.STUDENT)
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()

    # Address (PH format – province, not state)
    street_address = forms.CharField(max_length=255, required=False)
    barangay = forms.CharField(max_length=100, required=False)
    city_municipality = forms.CharField(max_length=100, label="City / Municipality")
    province = forms.CharField(max_length=100)
    postal_code = forms.CharField(max_length=10, required=False)

    # Student-only
    student_id_number = forms.CharField(max_length=50, required=False, label="Student ID number",
                                        widget=forms.TextInput(attrs={"placeholder": "2024-0012"}))
    course_program = forms.CharField(max_length=150, required=False, label="Course / Program",
                                     widget=forms.TextInput(attrs={"placeholder": "BS Information Systems"}))
    year_level = forms.TypedChoiceField(choices=[(i, f"Year {i}") for i in range(1, 6)], coerce=int,
                                        required=False, initial=1)
    emergency_contact_name = forms.CharField(max_length=150, required=False)
    emergency_contact_number = forms.CharField(max_length=20, required=False)

    STUDENT_FIELDS = ("student_id_number", "course_program")

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
        self.fields["role"].widget.attrs["class"] = "sr-only peer"

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this e-mail already exists.")
        return email

    def clean_student_id_number(self):
        value = (self.cleaned_data.get("student_id_number") or "").strip()
        if value and StudentProfile.objects.filter(student_id_number__iexact=value).exists():
            raise forms.ValidationError("This student ID is already registered.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("role") == User.Role.STUDENT:
            for name in self.STUDENT_FIELDS:
                if not cleaned.get(name):
                    self.add_error(name, "This field is required for students.")
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = self.cleaned_data["role"]
        user.status = User.AccountStatus.PENDING
        user.email = self.cleaned_data["email"]
        user.save()
        Address.objects.create(
            user=user,
            street_address=self.cleaned_data.get("street_address", ""),
            barangay=self.cleaned_data.get("barangay", ""),
            city_municipality=self.cleaned_data["city_municipality"],
            province=self.cleaned_data["province"],
            postal_code=self.cleaned_data.get("postal_code", ""),
        )
        if user.role == User.Role.STUDENT:
            StudentProfile.objects.create(
                user=user,
                student_id_number=self.cleaned_data["student_id_number"],
                course_program=self.cleaned_data["course_program"],
                year_level=self.cleaned_data.get("year_level") or 1,
                emergency_contact_name=self.cleaned_data.get("emergency_contact_name", ""),
                emergency_contact_number=self.cleaned_data.get("emergency_contact_number", ""),
            )
        return user


# ---------------------------------------------------------------------------
# Superadmin management forms
# ---------------------------------------------------------------------------
class UserAdminForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "username", "role", "status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class AddressForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = Address
        fields = ("street_address", "barangay", "city_municipality", "province", "postal_code")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class StudentProfileForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = StudentProfile
        fields = ("student_id_number", "course_program", "year_level",
                  "emergency_contact_name", "emergency_contact_number")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()


class SubjectForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = Subject
        fields = ("subject_code", "title", "teacher", "department")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teacher"].queryset = User.objects.filter(
            role=User.Role.INSTRUCTOR, status=User.AccountStatus.ACTIVE, is_active=True
        )
        self.fields["department"].queryset = Department.objects.filter(is_active=True)
        self._style_fields()


class DepartmentForm(TailwindMixin, forms.ModelForm):
    class Meta:
        model = Department
        fields = ("code", "name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()
