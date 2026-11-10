# SmartAttendance ERD

Entity-relationship diagram of the app's 9 tables (generated from `attendance/models.py`).
GitHub and VS Code (with a Mermaid preview extension) draw it automatically. To export a PNG/SVG,
paste the code block into <https://mermaid.live>.

```mermaid
erDiagram
    User ||--o| Address : "lives at"
    User ||--o| StudentProfile : "has profile"
    User ||--o{ Subject : "teaches"
    Department |o--o{ Subject : "offers"
    Subject ||--o{ SubjectEnrollment : "has"
    User ||--o{ SubjectEnrollment : "enrolls in"
    Subject ||--o{ ClassSession : "holds"
    ClassSession ||--o{ AttendanceLog : "records"
    User ||--o{ AttendanceLog : "checks in"
    ClassSession |o--o{ AuditScanLog : "flags"
    User |o--o{ AuditScanLog : "attempts"

    User {
        BIGINT id PK
        VARCHAR username UK "150"
        VARCHAR email UK "254"
        VARCHAR password "128, hashed"
        VARCHAR first_name "150"
        VARCHAR last_name "150"
        VARCHAR role "admin, instructor, student"
        VARCHAR status "pending, active, suspended"
        VARCHAR device_id UK "255, NULL = unbound, one account per phone"
        BOOLEAN is_active "False = archived"
        BOOLEAN is_staff
        BOOLEAN is_superuser
        DATETIME last_login "NULL"
        DATETIME date_joined
    }
    Address {
        BIGINT id PK
        BIGINT user_id FK, UK "CASCADE"
        VARCHAR street_address "255"
        VARCHAR barangay "100"
        VARCHAR city_municipality "100"
        VARCHAR province "100"
        VARCHAR postal_code "10"
    }
    StudentProfile {
        BIGINT id PK
        BIGINT user_id FK, UK "CASCADE"
        VARCHAR student_id_number UK "50"
        VARCHAR course_program "150"
        SMALLINT year_level
        VARCHAR emergency_contact_name "150"
        VARCHAR emergency_contact_number "20"
    }
    Department {
        BIGINT id PK
        VARCHAR code UK "20"
        VARCHAR name "150"
        BOOLEAN is_active
    }
    Subject {
        BIGINT id PK
        VARCHAR subject_code UK "20"
        VARCHAR title "200"
        BIGINT teacher_id FK "CASCADE"
        BIGINT department_id FK "SET NULL, NULL"
    }
    SubjectEnrollment {
        BIGINT id PK
        BIGINT subject_id FK "UNIQUE with student_id"
        BIGINT student_id FK "CASCADE"
        DATETIME enrolled_at
    }
    ClassSession {
        BIGINT id PK
        BIGINT subject_id FK "CASCADE"
        VARCHAR session_title "150"
        DATE date
        DATETIME created_at
        DATETIME expires_at
        DECIMAL instructor_latitude "9,6 NULL = geofence off"
        DECIMAL instructor_longitude "9,6 NULL = geofence off"
        INTEGER allowed_radius_meters "default 40"
        SMALLINT late_after_minutes "default 15, chosen per session"
        BOOLEAN is_active
    }
    AttendanceLog {
        BIGINT id PK
        BIGINT session_id FK "UNIQUE with student_id"
        BIGINT student_id FK "CASCADE"
        VARCHAR status "present, late, absent"
        DATETIME scanned_at
        VARCHAR scanned_device_id "255"
        TEXT notes
    }
    AuditScanLog {
        BIGINT id PK
        BIGINT session_id FK "CASCADE, NULL"
        BIGINT student_id FK "SET NULL, NULL"
        VARCHAR reason "5 rejection reasons"
        DECIMAL student_latitude "9,6 NULL"
        DECIMAL student_longitude "9,6 NULL"
        VARCHAR submitted_device_id "255 NULL"
        TEXT raw_payload
        DATETIME created_at
    }
```
