
from flask import (
    Flask, request, redirect, url_for, render_template,
    flash, Response, send_file, session, abort, jsonify
)
import sqlite3
import csv
import io
import os
from datetime import date
from pathlib import Path
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get(
    "EDUCONTROL_SECRET_KEY",
    "educontrol-v8-change-this-secret"
)

DB_NAME = "student_debts.db"

# set TEACHER_REGISTRATION_CODE=ваш_код
TEACHER_REGISTRATION_CODE = os.environ.get(
    "TEACHER_REGISTRATION_CODE",
    "123"
)


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def table_exists(conn, table_name):
    row = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table' AND name=?
        """,
        (table_name,)
    ).fetchone()
    return row is not None


def column_exists(conn, table_name, column_name):
    columns = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()
    return any(col["name"] == column_name for col in columns)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            group_id INTEGER NOT NULL,
            FOREIGN KEY (group_id)
                REFERENCES groups(id)
                ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subjects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_name TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject_id INTEGER NOT NULL,
            assignment_name TEXT NOT NULL,
            repayment_deadline DATE,
            status TEXT NOT NULL DEFAULT 'Не сдано',
            score REAL,
            max_score REAL,
            comment TEXT,
            created_at DATE NOT NULL,
            FOREIGN KEY (student_id)
                REFERENCES students(id)
                ON DELETE CASCADE,
            FOREIGN KEY (subject_id)
                REFERENCES subjects(id)
                ON DELETE CASCADE
        )
    """)

    # Пользователи системы.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('teacher', 'student')),
            display_name TEXT NOT NULL,
            student_id INTEGER UNIQUE,
            created_at DATE NOT NULL,
            FOREIGN KEY (student_id)
                REFERENCES students(id)
                ON DELETE SET NULL
        )
    """)

    # Совместимость с предыдущими версиями.
    if not column_exists(conn, "debts", "score"):
        cur.execute("ALTER TABLE debts ADD COLUMN score REAL")

    if not column_exists(conn, "debts", "max_score"):
        cur.execute("ALTER TABLE debts ADD COLUMN max_score REAL")

    # ID преподавателя, который СОЗДАЛ задолженность.
    # Это поле является автором записи и не меняется при редактировании.
    # Для старых записей значение останется NULL, потому что определить
    # автора задним числом невозможно.
    if not column_exists(conn, "debts", "teacher_user_id"):
        cur.execute("ALTER TABLE debts ADD COLUMN teacher_user_id INTEGER")

    for subject in [
        "Программирование",
        "Базы данных",
        "Математика",
        "Английский язык"
    ]:
        exists = find_subject_by_name(conn, subject)
        if not exists:
            cur.execute(
                "INSERT INTO subjects (subject_name) VALUES (?)",
                (subject,)
            )

    # Демо-аккаунт преподавателя для чистой базы.
    # Создаётся только если в таблице users ещё нет ни одного аккаунта.
    users_count = cur.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    if users_count == 0:
        cur.execute("""
            INSERT INTO users (
                username,
                password_hash,
                role,
                display_name,
                student_id,
                created_at
            )
            VALUES (?, ?, 'teacher', ?, NULL, ?)
        """, (
            "adi",
            generate_password_hash("654321"),
            "Adi",
            date.today().isoformat()
        ))

    conn.commit()
    conn.close()


# ============================================================
# ОБЩИЕ ФУНКЦИИ
# ============================================================

def normalize_text(value):
    if not value:
        return ""
    return " ".join(str(value).casefold().split())


def build_full_name(last_name, first_name, patronymic=""):
    parts = [
        (last_name or "").strip(),
        (first_name or "").strip(),
        (patronymic or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def parse_optional_number(value):
    value = (value or "").strip()
    if not value:
        return None
    return float(value.replace(",", "."))


def find_group_by_name(conn, group_name):
    target = normalize_text(group_name)

    for row in conn.execute(
        "SELECT id, group_name FROM groups"
    ).fetchall():
        if normalize_text(row["group_name"]) == target:
            return row

    return None


def find_subject_by_name(conn, subject_name):
    target = normalize_text(subject_name)

    for row in conn.execute(
        "SELECT id, subject_name FROM subjects"
    ).fetchall():
        if normalize_text(row["subject_name"]) == target:
            return row

    return None


def get_or_create_group(conn, group_name):
    group_name = " ".join(group_name.strip().split())
    row = find_group_by_name(conn, group_name)

    if row:
        return row["id"]

    cur = conn.execute(
        "INSERT INTO groups (group_name) VALUES (?)",
        (group_name,)
    )
    return cur.lastrowid


def get_or_create_subject(conn, subject_name):
    subject_name = " ".join(subject_name.strip().split())
    row = find_subject_by_name(conn, subject_name)

    if row:
        return row["id"]

    cur = conn.execute(
        "INSERT INTO subjects (subject_name) VALUES (?)",
        (subject_name,)
    )
    return cur.lastrowid


def find_student_in_group(conn, full_name, group_id):
    target = normalize_text(full_name)

    for row in conn.execute(
        """
        SELECT id, full_name
        FROM students
        WHERE group_id = ?
        """,
        (group_id,)
    ).fetchall():
        if normalize_text(row["full_name"]) == target:
            return row

    return None


def get_or_create_student(conn, full_name, group_name):
    group_id = get_or_create_group(conn, group_name)
    full_name = " ".join(full_name.strip().split())

    student = find_student_in_group(
        conn,
        full_name,
        group_id
    )

    if student:
        return student["id"]

    cur = conn.execute(
        """
        INSERT INTO students (full_name, group_id)
        VALUES (?, ?)
        """,
        (full_name, group_id)
    )
    return cur.lastrowid


def update_overdue():
    conn = get_db()

    conn.execute("""
        UPDATE debts
        SET status = 'Просрочено'
        WHERE repayment_deadline IS NOT NULL
          AND repayment_deadline < ?
          AND status NOT IN ('Сдано', 'Просрочено')
    """, (date.today().isoformat(),))

    conn.commit()
    conn.close()


def validate_score(score, max_score):
    if score is not None and score < 0:
        return "Баллы не могут быть отрицательными."

    if max_score is not None and max_score <= 0:
        return "Максимальный балл должен быть больше нуля."

    if (
        score is not None
        and max_score is not None
        and score > max_score
    ):
        return "Набранные баллы не могут быть больше максимальных."

    return None


def common_counts(conn):
    row = conn.execute("""
        SELECT
            (SELECT COUNT(*) FROM students) AS students_count,
            (SELECT COUNT(*) FROM groups) AS groups_count,
            (SELECT COUNT(*) FROM subjects) AS subjects_count,
            (SELECT COUNT(*) FROM debts) AS debts_count,
            (
                SELECT COUNT(*)
                FROM debts
                WHERE status != 'Сдано'
            ) AS active_count,
            (
                SELECT COUNT(*)
                FROM debts
                WHERE status = 'Просрочено'
            ) AS overdue_count,
            (
                SELECT COUNT(*)
                FROM debts
                WHERE status = 'Сдано'
            ) AS completed_count
    """).fetchone()

    return {
        "students": row["students_count"] or 0,
        "groups": row["groups_count"] or 0,
        "subjects": row["subjects_count"] or 0,
        "debts": row["debts_count"] or 0,
        "active": row["active_count"] or 0,
        "overdue": row["overdue_count"] or 0,
        "completed": row["completed_count"] or 0,
    }


def get_filtered_debts(
    conn,
    search="",
    group="",
    status="",
    subject=""
):
    query = """
        SELECT
            debts.*,
            students.full_name,
            students.id AS student_id_real,
            groups.group_name,
            subjects.subject_name,
            teacher.display_name AS teacher_name
        FROM debts
        JOIN students
            ON debts.student_id = students.id
        JOIN groups
            ON students.group_id = groups.id
        JOIN subjects
            ON debts.subject_id = subjects.id
        LEFT JOIN users AS teacher
            ON debts.teacher_user_id = teacher.id
            AND teacher.role = 'teacher'
        WHERE 1=1
    """

    params = []

    if group:
        query += " AND groups.group_name = ?"
        params.append(group)

    if status:
        query += " AND debts.status = ?"
        params.append(status)

    if subject:
        query += " AND subjects.subject_name = ?"
        params.append(subject)

    query += """
        ORDER BY
            CASE
                WHEN debts.status = 'Просрочено' THEN 0
                WHEN debts.status = 'Не сдано' THEN 1
                WHEN debts.status = 'На пересдаче' THEN 2
                ELSE 3
            END,
            debts.repayment_deadline IS NULL,
            debts.repayment_deadline,
            debts.id DESC
    """

    debts = conn.execute(query, params).fetchall()

    if search:
        tokens = normalize_text(search).split()

        debts = [
            debt for debt in debts
            if all(
                token in normalize_text(debt["full_name"])
                for token in tokens
            )
        ]

    return debts


# ============================================================
# АВТОРИЗАЦИЯ
# ============================================================

def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    conn = get_db()

    user = conn.execute("""
        SELECT
            users.*,
            students.full_name AS student_full_name,
            groups.group_name AS student_group_name
        FROM users
        LEFT JOIN students
            ON users.student_id = students.id
        LEFT JOIN groups
            ON students.group_id = groups.id
        WHERE users.id = ?
    """, (user_id,)).fetchone()

    conn.close()
    return user


@app.context_processor
def inject_user():
    return {
        "current_user": current_user()
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id") or not current_user():
            # Сессия устарела или пользователь удалён из базы
            session.clear()
            flash(
                "Сначала войдите в систему.",
                "error"
            )
            return redirect(url_for("login"))

        return view(*args, **kwargs)

    return wrapped


def teacher_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()

        if not user:
            session.clear()
            flash(
                "Сначала войдите в систему.",
                "error"
            )
            return redirect(url_for("login"))

        if user["role"] != "teacher":
            abort(403)

        return view(*args, **kwargs)

    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get(
            "username",
            ""
        ).strip()

        password = request.form.get(
            "password",
            ""
        )

        conn = get_db()

        user = conn.execute("""
            SELECT *
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()

        conn.close()

        if (
            not user
            or not check_password_hash(
                user["password_hash"],
                password
            )
        ):
            flash(
                "Неверный логин или пароль.",
                "error"
            )
            return redirect(url_for("login"))

        session.clear()
        session["user_id"] = user["id"]

        flash(
            f"Добро пожаловать, {user['display_name']}!",
            "success"
        )

        return redirect(url_for("dashboard"))

    return render_template(
        "login.html"
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))

    conn = get_db()

    groups = conn.execute(
        "SELECT * FROM groups ORDER BY group_name"
    ).fetchall()

    form_data = {
        "username": "",
        "password": "",
        "password2": "",
        "role": "student",
        "last_name": "",
        "first_name": "",
        "patronymic": "",
        "group_name": "",
        "teacher_name": "",
        "teacher_code": "",
    }

    errors = {}

    if request.method == "POST":
        for key in form_data:
            form_data[key] = request.form.get(key, "")

        # ----------------------------
        # Общие поля
        # ----------------------------
        username = form_data["username"].strip()
        password = form_data["password"]
        password2 = form_data["password2"]
        role = form_data["role"]

        if not username:
            errors["username"] = "Введите логин."
        elif len(username) < 3:
            errors["username"] = "Минимум 3 символа."
        elif len(username) > 30:
            errors["username"] = "Максимум 30 символов."

        if not password:
            errors["password"] = "Введите пароль."
        elif len(password) < 6:
            errors["password"] = "Минимум 6 символов."

        if not password2:
            errors["password2"] = "Повторите пароль."
        elif password != password2:
            errors["password2"] = "Пароли не совпадают."

        if role not in {"student", "teacher"}:
            role = "student"
            form_data["role"] = "student"

        existing_user = None

        if username:
            existing_user = conn.execute("""
                SELECT id
                FROM users
                WHERE username = ?
            """, (username,)).fetchone()

        if existing_user:
            errors["username"] = "Такой логин уже занят."

        # ----------------------------
        # Преподаватель
        # ----------------------------
        if role == "teacher":
            teacher_name = form_data["teacher_name"].strip()
            teacher_code = form_data["teacher_code"].strip()

            if not teacher_name:
                errors["teacher_name"] = "Введите ФИО преподавателя."
            elif len(teacher_name) < 3:
                errors["teacher_name"] = "Введите не менее 3 символов."

            if not teacher_code:
                errors["teacher_code"] = "Введите код преподавателя."
            elif teacher_code != TEACHER_REGISTRATION_CODE:
                errors["teacher_code"] = "Неверный код преподавателя."

        # ----------------------------
        # Студент
        # ----------------------------
        else:
            last_name = form_data["last_name"].strip()
            first_name = form_data["first_name"].strip()
            patronymic = form_data["patronymic"].strip()
            group_name = form_data["group_name"].strip()

            if not last_name:
                errors["last_name"] = "Введите фамилию."
            elif len(last_name) < 2:
                errors["last_name"] = "Минимум 2 символа."

            if not first_name:
                errors["first_name"] = "Введите имя."
            elif len(first_name) < 2:
                errors["first_name"] = "Минимум 2 символа."

            if patronymic and len(patronymic) < 2:
                errors["patronymic"] = (
                    "Если указываете отчество — минимум 2 символа."
                )

            if not group_name:
                errors["group_name"] = "Введите группу."

        # Если есть хотя бы одна ошибка — НЕ редиректим.
        # Рендерим ту же страницу и сохраняем все введённые данные.
        if errors:
            conn.close()

            return render_template(
                "register.html",
                groups=groups,
                form_data=form_data,
                errors=errors
            )

        # ----------------------------
        # Создание аккаунта
        # ----------------------------
        if role == "teacher":
            display_name = form_data["teacher_name"].strip()

            cur = conn.execute("""
                INSERT INTO users (
                    username,
                    password_hash,
                    role,
                    display_name,
                    student_id,
                    created_at
                )
                VALUES (?, ?, 'teacher', ?, NULL, ?)
            """, (
                username,
                generate_password_hash(password),
                display_name,
                date.today().isoformat()
            ))

            user_id = cur.lastrowid

        else:
            last_name = form_data["last_name"].strip()
            first_name = form_data["first_name"].strip()
            patronymic = form_data["patronymic"].strip()
            group_name = form_data["group_name"].strip()

            full_name = build_full_name(
                last_name,
                first_name,
                patronymic
            )

            group_id = get_or_create_group(
                conn,
                group_name
            )

            student = find_student_in_group(
                conn,
                full_name,
                group_id
            )

            if student:
                student_id = student["id"]
            else:
                cur = conn.execute("""
                    INSERT INTO students (
                        full_name,
                        group_id
                    )
                    VALUES (?, ?)
                """, (
                    full_name,
                    group_id
                ))

                student_id = cur.lastrowid

            linked_user = conn.execute("""
                SELECT id
                FROM users
                WHERE student_id = ?
            """, (student_id,)).fetchone()

            if linked_user:
                conn.close()

                errors["last_name"] = (
                    "Для этого студента аккаунт уже создан."
                )

                return render_template(
                    "register.html",
                    groups=groups,
                    form_data=form_data,
                    errors=errors
                )

            cur = conn.execute("""
                INSERT INTO users (
                    username,
                    password_hash,
                    role,
                    display_name,
                    student_id,
                    created_at
                )
                VALUES (?, ?, 'student', ?, ?, ?)
            """, (
                username,
                generate_password_hash(password),
                full_name,
                student_id,
                date.today().isoformat()
            ))

            user_id = cur.lastrowid

        conn.commit()
        conn.close()

        session.clear()
        session["user_id"] = user_id

        flash(
            "Регистрация завершена.",
            "success"
        )

        return redirect(url_for("dashboard"))

    conn.close()

    return render_template(
        "register.html",
        groups=groups,
        form_data=form_data,
        errors=errors
    )


@app.route("/logout")
@login_required
def logout():
    session.clear()

    flash(
        "Вы вышли из системы.",
        "success"
    )

    return redirect(url_for("login"))


# ============================================================
# DASHBOARD
# ============================================================

@app.route("/")
@login_required
def dashboard():
    update_overdue()
    user = current_user()

    if user["role"] == "student":
        return redirect(
            url_for("my_profile")
        )

    conn = get_db()

    counts = common_counts(conn)

    total = counts["debts"]
    completed = counts["completed"]

    completion_rate = (
        round((completed / total) * 100)
        if total
        else 0
    )

    recent_debts = conn.execute("""
        SELECT
            debts.id,
            students.id AS student_id,
            students.full_name,
            groups.group_name,
            subjects.subject_name,
            debts.assignment_name,
            debts.status,
            debts.repayment_deadline,
            teacher.display_name AS teacher_name
        FROM debts
        JOIN students
            ON debts.student_id = students.id
        JOIN groups
            ON students.group_id = groups.id
        JOIN subjects
            ON debts.subject_id = subjects.id
        LEFT JOIN users AS teacher
            ON debts.teacher_user_id = teacher.id
            AND teacher.role = 'teacher'
        ORDER BY debts.id DESC
        LIMIT 6
    """).fetchall()

    top_groups = conn.execute("""
        SELECT
            groups.group_name,
            COUNT(debts.id) AS debt_count
        FROM groups
        LEFT JOIN students
            ON students.group_id = groups.id
        LEFT JOIN debts
            ON debts.student_id = students.id
            AND debts.status != 'Сдано'
        GROUP BY groups.id, groups.group_name
        ORDER BY debt_count DESC, groups.group_name
        LIMIT 5
    """).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        active_page="dashboard",
        counts=counts,
        completion_rate=completion_rate,
        recent_debts=recent_debts,
        top_groups=top_groups,
        today=date.today().strftime("%d.%m.%Y"),
    )


# ============================================================
# СТУДЕНТСКИЙ КАБИНЕТ
# ============================================================

@app.route("/my")
@login_required
def my_profile():
    update_overdue()
    user = current_user()

    if user["role"] != "student":
        return redirect(
            url_for("dashboard")
        )

    if not user["student_id"]:
        flash(
            "Ваш аккаунт не связан с карточкой студента.",
            "error"
        )
        return redirect(
            url_for("logout")
        )

    conn = get_db()

    student = conn.execute("""
        SELECT
            students.id,
            students.full_name,
            groups.group_name
        FROM students
        JOIN groups
            ON students.group_id = groups.id
        WHERE students.id = ?
    """, (
        user["student_id"],
    )).fetchone()

    debts = conn.execute("""
        SELECT
            debts.*,
            subjects.subject_name,
            teacher.display_name AS teacher_name
        FROM debts
        JOIN subjects
            ON debts.subject_id = subjects.id
        LEFT JOIN users AS teacher
            ON debts.teacher_user_id = teacher.id
            AND teacher.role = 'teacher'
        WHERE debts.student_id = ?
        ORDER BY
            CASE
                WHEN debts.status = 'Просрочено' THEN 0
                WHEN debts.status = 'Не сдано' THEN 1
                WHEN debts.status = 'На пересдаче' THEN 2
                ELSE 3
            END,
            debts.id DESC
    """, (
        user["student_id"],
    )).fetchall()

    stats = {
        "total": len(debts),
        "active": sum(
            1 for debt in debts
            if debt["status"] != "Сдано"
        ),
        "overdue": sum(
            1 for debt in debts
            if debt["status"] == "Просрочено"
        ),
        "completed": sum(
            1 for debt in debts
            if debt["status"] == "Сдано"
        ),
    }

    conn.close()

    return render_template(
        "my_profile.html",
        active_page="my",
        student=student,
        debts=debts,
        stats=stats
    )


# ============================================================
# ПРЕПОДАВАТЕЛИ — ДОСТУПНО ВСЕМ АВТОРИЗОВАННЫМ
# ============================================================

@app.route("/teachers")
@login_required
def teachers_page():
    conn = get_db()

    search = request.args.get(
        "search",
        ""
    ).strip()

    teachers = conn.execute("""
        SELECT
            users.id,
            users.display_name,
            users.username,
            users.created_at,
            COUNT(debts.id) AS debt_count
        FROM users
        LEFT JOIN debts
            ON debts.teacher_user_id = users.id
        WHERE users.role = 'teacher'
        GROUP BY
            users.id,
            users.display_name,
            users.username,
            users.created_at
        ORDER BY users.display_name
    """).fetchall()

    if search:
        tokens = normalize_text(search).split()

        teachers = [
            teacher
            for teacher in teachers
            if all(
                token in normalize_text(
                    " ".join([
                        teacher["display_name"] or "",
                        teacher["username"] or "",
                    ])
                )
                for token in tokens
            )
        ]

    conn.close()

    return render_template(
        "teachers.html",
        active_page="teachers",
        teachers=teachers,
        search=search
    )


@app.route("/teachers/<int:teacher_id>")
@login_required
def teacher_debts(teacher_id):
    """Задолженности, добавленные конкретным преподавателем.

    Студент видит только свои долги от этого преподавателя.
    Преподаватель видит все долги, которые добавил выбранный коллега.
    """
    update_overdue()
    user = current_user()
    conn = get_db()

    teacher = conn.execute("""
        SELECT id, display_name, username
        FROM users
        WHERE id = ? AND role = 'teacher'
    """, (teacher_id,)).fetchone()

    if not teacher:
        conn.close()
        abort(404)

    query = """
        SELECT
            debts.*,
            students.full_name,
            students.id AS student_id_real,
            groups.group_name,
            subjects.subject_name
        FROM debts
        JOIN students
            ON debts.student_id = students.id
        JOIN groups
            ON students.group_id = groups.id
        JOIN subjects
            ON debts.subject_id = subjects.id
        WHERE debts.teacher_user_id = ?
    """
    params = [teacher_id]

    if user["role"] == "student":
        # Студент никогда не видит чужие долги
        query += " AND debts.student_id = ?"
        params.append(user["student_id"])

    query += """
        ORDER BY
            CASE
                WHEN debts.status = 'Просрочено' THEN 0
                WHEN debts.status = 'Не сдано' THEN 1
                WHEN debts.status = 'На пересдаче' THEN 2
                ELSE 3
            END,
            debts.repayment_deadline IS NULL,
            debts.repayment_deadline,
            debts.id DESC
    """

    debts = conn.execute(query, params).fetchall()
    conn.close()

    stats = {
        "total": len(debts),
        "active": sum(1 for d in debts if d["status"] != "Сдано"),
        "overdue": sum(1 for d in debts if d["status"] == "Просрочено"),
        "completed": sum(1 for d in debts if d["status"] == "Сдано"),
    }

    return render_template(
        "teacher_debts.html",
        active_page="teachers",
        teacher=teacher,
        debts=debts,
        stats=stats
    )


# ============================================================
# ЗАДОЛЖЕННОСТИ — ТОЛЬКО ПРЕПОДАВАТЕЛЬ
# ============================================================

@app.route("/debts")
@teacher_required
def debts_page():
    update_overdue()
    conn = get_db()

    search = request.args.get(
        "search",
        ""
    ).strip()

    group = request.args.get(
        "group",
        ""
    ).strip()

    status = request.args.get(
        "status",
        ""
    ).strip()

    subject = request.args.get(
        "subject",
        ""
    ).strip()

    debts = get_filtered_debts(
        conn,
        search=search,
        group=group,
        status=status,
        subject=subject
    )

    groups = conn.execute("""
        SELECT *
        FROM groups
        ORDER BY group_name
    """).fetchall()

    subjects = conn.execute("""
        SELECT *
        FROM subjects
        ORDER BY subject_name
    """).fetchall()

    conn.close()

    return render_template(
        "debts.html",
        active_page="debts",
        debts=debts,
        groups=groups,
        subjects=subjects,
        filters={
            "search": search,
            "group": group,
            "status": status,
            "subject": subject,
        }
    )


@app.route("/debts/new", methods=["GET", "POST"])
@teacher_required
def new_debt():
    conn = get_db()

    if request.method == "POST":
        last_name = request.form.get(
            "last_name",
            ""
        ).strip()

        first_name = request.form.get(
            "first_name",
            ""
        ).strip()

        patronymic = request.form.get(
            "patronymic",
            ""
        ).strip()

        group_name = request.form.get(
            "group_name",
            ""
        ).strip()

        subject_name = request.form.get(
            "subject_name",
            ""
        ).strip()

        assignment_name = request.form.get(
            "assignment_name",
            ""
        ).strip()

        if not last_name or not first_name:
            conn.close()
            flash(
                "Фамилия и имя обязательны.",
                "error"
            )
            return redirect(
                url_for("new_debt")
            )

        if (
            not group_name
            or not subject_name
            or not assignment_name
        ):
            conn.close()
            flash(
                "Заполните группу, предмет и задание.",
                "error"
            )
            return redirect(
                url_for("new_debt")
            )

        try:
            score = parse_optional_number(
                request.form.get("score")
            )
            max_score = parse_optional_number(
                request.form.get("max_score")
            )
        except ValueError:
            conn.close()
            flash(
                "Баллы должны быть числом.",
                "error"
            )
            return redirect(
                url_for("new_debt")
            )

        error = validate_score(
            score,
            max_score
        )

        if error:
            conn.close()
            flash(
                error,
                "error"
            )
            return redirect(
                url_for("new_debt")
            )

        full_name = build_full_name(
            last_name,
            first_name,
            patronymic
        )

        student_id = get_or_create_student(
            conn,
            full_name,
            group_name
        )

        subject_id = get_or_create_subject(
            conn,
            subject_name
        )

        # Автор задолженности определяется автоматически:
        # это преподаватель, который сейчас вошёл в систему
        # и нажал "Сохранить".
        creator = current_user()

        conn.execute("""
            INSERT INTO debts (
                student_id,
                subject_id,
                assignment_name,
                repayment_deadline,
                status,
                score,
                max_score,
                comment,
                created_at,
                teacher_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            student_id,
            subject_id,
            assignment_name,
            request.form.get(
                "repayment_deadline"
            ) or None,
            request.form.get(
                "status",
                "Не сдано"
            ),
            score,
            max_score,
            request.form.get(
                "comment",
                ""
            ).strip(),
            date.today().isoformat(),
            creator["id"]
        ))

        conn.commit()
        conn.close()

        flash(
            "Задолженность добавлена.",
            "success"
        )

        return redirect(
            url_for("debts_page")
        )

    groups = conn.execute("""
        SELECT *
        FROM groups
        ORDER BY group_name
    """).fetchall()

    subjects = conn.execute("""
        SELECT *
        FROM subjects
        ORDER BY subject_name
    """).fetchall()

    conn.close()

    return render_template(
        "new_debt.html",
        active_page="debts",
        groups=groups,
        subjects=subjects,
    )


@app.route(
    "/debts/<int:debt_id>/edit",
    methods=["GET", "POST"]
)
@teacher_required
def edit_debt(debt_id):
    conn = get_db()

    debt = conn.execute("""
        SELECT
            debts.*,
            students.full_name,
            groups.group_name,
            subjects.subject_name,
            teacher.display_name AS teacher_name
        FROM debts
        JOIN students
            ON debts.student_id = students.id
        JOIN groups
            ON students.group_id = groups.id
        JOIN subjects
            ON debts.subject_id = subjects.id
        LEFT JOIN users AS teacher
            ON debts.teacher_user_id = teacher.id
            AND teacher.role = 'teacher'
        WHERE debts.id = ?
    """, (
        debt_id,
    )).fetchone()

    if not debt:
        conn.close()
        flash(
            "Задолженность не найдена.",
            "error"
        )
        return redirect(
            url_for("debts_page")
        )

    if request.method == "POST":
        subject_name = request.form.get(
            "subject_name",
            ""
        ).strip()

        assignment_name = request.form.get(
            "assignment_name",
            ""
        ).strip()

        try:
            score = parse_optional_number(
                request.form.get("score")
            )
            max_score = parse_optional_number(
                request.form.get("max_score")
            )
        except ValueError:
            conn.close()
            flash(
                "Баллы должны быть числом.",
                "error"
            )
            return redirect(
                url_for(
                    "edit_debt",
                    debt_id=debt_id
                )
            )

        error = validate_score(
            score,
            max_score
        )

        if error:
            conn.close()
            flash(
                error,
                "error"
            )
            return redirect(
                url_for(
                    "edit_debt",
                    debt_id=debt_id
                )
            )

        subject_id = get_or_create_subject(
            conn,
            subject_name
        )

        # ВАЖНО:
        # teacher_user_id здесь намеренно НЕ обновляется.
        # Даже если запись редактирует другой преподаватель,
        # автором остаётся тот, кто создал задолженность.
        conn.execute("""
            UPDATE debts
            SET
                subject_id = ?,
                assignment_name = ?,
                repayment_deadline = ?,
                status = ?,
                score = ?,
                max_score = ?,
                comment = ?
            WHERE id = ?
        """, (
            subject_id,
            assignment_name,
            request.form.get(
                "repayment_deadline"
            ) or None,
            request.form.get(
                "status",
                "Не сдано"
            ),
            score,
            max_score,
            request.form.get(
                "comment",
                ""
            ).strip(),
            debt_id
        ))

        conn.commit()
        conn.close()

        flash(
            "Задолженность обновлена.",
            "success"
        )

        return redirect(
            url_for("debts_page")
        )

    subjects = conn.execute("""
        SELECT *
        FROM subjects
        ORDER BY subject_name
    """).fetchall()

    conn.close()

    return render_template(
        "edit_debt.html",
        active_page="debts",
        debt=debt,
        subjects=subjects
    )


# ============================================================
# СТУДЕНТЫ — ТОЛЬКО ПРЕПОДАВАТЕЛЬ
# ============================================================

@app.route("/students")
@teacher_required
def students_page():
    conn = get_db()

    search = request.args.get(
        "search",
        ""
    ).strip()

    group_filter = request.args.get(
        "group",
        ""
    ).strip()

    groups = conn.execute("""
        SELECT
            groups.id,
            groups.group_name,
            COUNT(students.id) AS student_count
        FROM groups
        LEFT JOIN students
            ON students.group_id = groups.id
        GROUP BY groups.id, groups.group_name
        ORDER BY groups.group_name
    """).fetchall()

    # Правильное разделение студентов по группам:
    # отдельная структура group -> students.
    grouped_students = []

    for group in groups:
        query = """
            SELECT
                students.id,
                students.full_name,
                groups.group_name,
                COUNT(debts.id) AS debt_count,
                SUM(
                    CASE
                        WHEN debts.status != 'Сдано'
                        THEN 1
                        ELSE 0
                    END
                ) AS active_debt_count,
                users.id AS account_id,
                users.username AS account_username
            FROM students
            JOIN groups
                ON students.group_id = groups.id
            LEFT JOIN debts
                ON debts.student_id = students.id
            LEFT JOIN users
                ON users.student_id = students.id
            WHERE students.group_id = ?
            GROUP BY
                students.id,
                students.full_name,
                groups.group_name,
                users.id,
                users.username
            ORDER BY students.full_name
        """

        students = conn.execute(
            query,
            (group["id"],)
        ).fetchall()

        if search:
            tokens = normalize_text(
                search
            ).split()

            students = [
                student
                for student in students
                if all(
                    token in normalize_text(
                        student["full_name"]
                    )
                    for token in tokens
                )
            ]

        if (
            group_filter
            and group["group_name"] != group_filter
        ):
            continue

        if students or not search:
            grouped_students.append({
                "group": group,
                "students": students
            })

    conn.close()

    return render_template(
        "students.html",
        active_page="students",
        grouped_students=grouped_students,
        groups=groups,
        filters={
            "search": search,
            "group": group_filter,
        }
    )


@app.route(
    "/students/add",
    methods=["POST"]
)
@teacher_required
def add_student():
    last_name = request.form.get(
        "last_name",
        ""
    ).strip()

    first_name = request.form.get(
        "first_name",
        ""
    ).strip()

    patronymic = request.form.get(
        "patronymic",
        ""
    ).strip()

    group_name = request.form.get(
        "group_name",
        ""
    ).strip()

    if (
        not last_name
        or not first_name
        or not group_name
    ):
        flash(
            "Фамилия, имя и группа обязательны.",
            "error"
        )
        return redirect(
            url_for("students_page")
        )

    full_name = build_full_name(
        last_name,
        first_name,
        patronymic
    )

    conn = get_db()

    group_id = get_or_create_group(
        conn,
        group_name
    )

    existing = find_student_in_group(
        conn,
        full_name,
        group_id
    )

    if existing:
        flash(
            "Такой студент уже есть в этой группе.",
            "error"
        )
    else:
        conn.execute("""
            INSERT INTO students (
                full_name,
                group_id
            )
            VALUES (?, ?)
        """, (
            full_name,
            group_id
        ))

        conn.commit()

        flash(
            "Студент добавлен.",
            "success"
        )

    conn.close()

    return redirect(
        url_for("students_page")
    )


@app.route(
    "/students/<int:student_id>"
)
@teacher_required
def student_profile(student_id):
    update_overdue()
    conn = get_db()

    student = conn.execute("""
        SELECT
            students.id,
            students.full_name,
            groups.group_name,
            users.username,
            users.id AS account_id
        FROM students
        JOIN groups
            ON students.group_id = groups.id
        LEFT JOIN users
            ON users.student_id = students.id
        WHERE students.id = ?
    """, (
        student_id,
    )).fetchone()

    if not student:
        conn.close()
        flash(
            "Студент не найден.",
            "error"
        )
        return redirect(
            url_for("students_page")
        )

    debts = conn.execute("""
        SELECT
            debts.*,
            subjects.subject_name,
            teacher.display_name AS teacher_name
        FROM debts
        JOIN subjects
            ON debts.subject_id = subjects.id
        LEFT JOIN users AS teacher
            ON debts.teacher_user_id = teacher.id
            AND teacher.role = 'teacher'
        WHERE debts.student_id = ?
        ORDER BY debts.id DESC
    """, (
        student_id,
    )).fetchall()

    stats = {
        "total": len(debts),
        "active": sum(
            1 for debt in debts
            if debt["status"] != "Сдано"
        ),
        "overdue": sum(
            1 for debt in debts
            if debt["status"] == "Просрочено"
        ),
        "completed": sum(
            1 for debt in debts
            if debt["status"] == "Сдано"
        ),
    }

    conn.close()

    return render_template(
        "student.html",
        active_page="students",
        student=student,
        debts=debts,
        stats=stats
    )


# ============================================================
# ГРУППЫ — ТОЛЬКО ПРЕПОДАВАТЕЛЬ
# ============================================================

@app.route("/groups")
@teacher_required
def groups_page():
    conn = get_db()

    groups = conn.execute("""
        SELECT
            groups.id,
            groups.group_name,
            COUNT(
                DISTINCT students.id
            ) AS student_count,
            COUNT(debts.id) AS debt_count,
            SUM(
                CASE
                    WHEN debts.status != 'Сдано'
                    THEN 1
                    ELSE 0
                END
            ) AS active_debt_count
        FROM groups
        LEFT JOIN students
            ON students.group_id = groups.id
        LEFT JOIN debts
            ON debts.student_id = students.id
        GROUP BY
            groups.id,
            groups.group_name
        ORDER BY groups.group_name
    """).fetchall()

    conn.close()

    return render_template(
        "groups.html",
        active_page="groups",
        groups=groups
    )


@app.route(
    "/groups/add",
    methods=["POST"]
)
@teacher_required
def add_group():
    group_name = request.form.get(
        "group_name",
        ""
    ).strip()

    if not group_name:
        flash(
            "Введите название группы.",
            "error"
        )
        return redirect(
            url_for("groups_page")
        )

    conn = get_db()

    if find_group_by_name(
        conn,
        group_name
    ):
        flash(
            "Такая группа уже существует.",
            "error"
        )
    else:
        conn.execute("""
            INSERT INTO groups (
                group_name
            )
            VALUES (?)
        """, (
            " ".join(
                group_name.split()
            ),
        ))

        conn.commit()

        flash(
            "Группа добавлена.",
            "success"
        )

    conn.close()

    return redirect(
        url_for("groups_page")
    )


# ============================================================
# ПРЕДМЕТЫ — ТОЛЬКО ПРЕПОДАВАТЕЛЬ
# ============================================================

@app.route("/subjects")
@teacher_required
def subjects_page():
    conn = get_db()

    subjects = conn.execute("""
        SELECT
            subjects.id,
            subjects.subject_name,
            COUNT(debts.id) AS debt_count,
            SUM(
                CASE
                    WHEN debts.status != 'Сдано'
                    THEN 1
                    ELSE 0
                END
            ) AS active_debt_count
        FROM subjects
        LEFT JOIN debts
            ON debts.subject_id = subjects.id
        GROUP BY
            subjects.id,
            subjects.subject_name
        ORDER BY subjects.subject_name
    """).fetchall()

    conn.close()

    return render_template(
        "subjects.html",
        active_page="subjects",
        subjects=subjects
    )


@app.route(
    "/subjects/add",
    methods=["POST"]
)
@teacher_required
def add_subject():
    subject_name = request.form.get(
        "subject_name",
        ""
    ).strip()

    if not subject_name:
        flash(
            "Введите название предмета.",
            "error"
        )
        return redirect(
            url_for("subjects_page")
        )

    conn = get_db()

    if find_subject_by_name(
        conn,
        subject_name
    ):
        flash(
            "Такой предмет уже существует.",
            "error"
        )
    else:
        conn.execute("""
            INSERT INTO subjects (
                subject_name
            )
            VALUES (?)
        """, (
            " ".join(
                subject_name.split()
            ),
        ))

        conn.commit()

        flash(
            "Предмет добавлен.",
            "success"
        )

    conn.close()

    return redirect(
        url_for("subjects_page")
    )


# ============================================================
# УДАЛЕНИЕ / СТАТУСЫ
# ============================================================

@app.route(
    "/debts/<int:debt_id>/status",
    methods=["POST"]
)
@teacher_required
def change_status(debt_id):
    status = request.form.get(
        "status",
        "Не сдано"
    )

    allowed = {
        "Не сдано",
        "На пересдаче",
        "Просрочено",
        "Сдано"
    }

    if status not in allowed:
        status = "Не сдано"

    conn = get_db()

    conn.execute("""
        UPDATE debts
        SET status = ?
        WHERE id = ?
    """, (
        status,
        debt_id
    ))

    conn.commit()
    conn.close()

    flash(
        "Статус обновлён.",
        "success"
    )

    return redirect(
        request.referrer
        or url_for("debts_page")
    )


@app.route(
    "/debts/<int:debt_id>/delete",
    methods=["POST"]
)
@teacher_required
def delete_debt(debt_id):
    conn = get_db()

    conn.execute("""
        DELETE FROM debts
        WHERE id = ?
    """, (
        debt_id,
    ))

    conn.commit()
    conn.close()

    flash(
        "Задолженность удалена.",
        "success"
    )

    return redirect(
        request.referrer
        or url_for("debts_page")
    )


@app.route(
    "/students/<int:student_id>/delete",
    methods=["POST"]
)
@teacher_required
def delete_student(student_id):
    conn = get_db()

    # Если у студента есть аккаунт, удаляем аккаунт,
    # чтобы не остался "висячий" пользователь.
    conn.execute("""
        DELETE FROM users
        WHERE student_id = ?
    """, (
        student_id,
    ))

    conn.execute("""
        DELETE FROM students
        WHERE id = ?
    """, (
        student_id,
    ))

    conn.commit()
    conn.close()

    flash(
        "Студент удалён.",
        "success"
    )

    return redirect(
        url_for("students_page")
    )


@app.route(
    "/groups/<int:group_id>/delete",
    methods=["POST"]
)
@teacher_required
def delete_group(group_id):
    conn = get_db()

    student_ids = [
        row["id"]
        for row in conn.execute("""
            SELECT id
            FROM students
            WHERE group_id = ?
        """, (
            group_id,
        )).fetchall()
    ]

    for student_id in student_ids:
        conn.execute("""
            DELETE FROM users
            WHERE student_id = ?
        """, (
            student_id,
        ))

    conn.execute("""
        DELETE FROM groups
        WHERE id = ?
    """, (
        group_id,
    ))

    conn.commit()
    conn.close()

    flash(
        "Группа удалена.",
        "success"
    )

    return redirect(
        url_for("groups_page")
    )


@app.route(
    "/subjects/<int:subject_id>/delete",
    methods=["POST"]
)
@teacher_required
def delete_subject(subject_id):
    conn = get_db()

    debt_count = conn.execute("""
        SELECT COUNT(*)
        FROM debts
        WHERE subject_id = ?
    """, (
        subject_id,
    )).fetchone()[0]

    if debt_count:
        conn.close()

        flash(
            "Нельзя удалить предмет: по нему есть задолженности.",
            "error"
        )

        return redirect(
            url_for("subjects_page")
        )

    conn.execute("""
        DELETE FROM subjects
        WHERE id = ?
    """, (
        subject_id,
    ))

    conn.commit()
    conn.close()

    flash(
        "Предмет удалён.",
        "success"
    )

    return redirect(
        url_for("subjects_page")
    )


# ============================================================
# ОЧИСТКА СПИСКОВ — ТОЛЬКО ПРЕПОДАВАТЕЛЬ
# ============================================================

@app.route(
    "/clear/debts",
    methods=["POST"]
)
@teacher_required
def clear_debts():
    conn = get_db()
    conn.execute("DELETE FROM debts")
    conn.commit()
    conn.close()

    flash(
        "Список задолженностей очищен.",
        "success"
    )

    return redirect(
        url_for("debts_page")
    )


@app.route(
    "/clear/students",
    methods=["POST"]
)
@teacher_required
def clear_students():
    conn = get_db()

    conn.execute("""
        DELETE FROM users
        WHERE role = 'student'
    """)

    conn.execute(
        "DELETE FROM students"
    )

    conn.commit()
    conn.close()

    flash(
        "Список студентов и их аккаунтов очищен.",
        "success"
    )

    return redirect(
        url_for("students_page")
    )


@app.route(
    "/clear/groups",
    methods=["POST"]
)
@teacher_required
def clear_groups():
    conn = get_db()

    conn.execute("""
        DELETE FROM users
        WHERE role = 'student'
    """)

    conn.execute(
        "DELETE FROM groups"
    )

    conn.commit()
    conn.close()

    flash(
        "Группы, студенты и их аккаунты очищены.",
        "success"
    )

    return redirect(
        url_for("groups_page")
    )


@app.route(
    "/clear/subjects",
    methods=["POST"]
)
@teacher_required
def clear_subjects():
    conn = get_db()

    conn.execute(
        "DELETE FROM subjects"
    )

    conn.commit()
    conn.close()

    flash(
        "Предметы и связанные задолженности очищены.",
        "success"
    )

    return redirect(
        url_for("subjects_page")
    )


# ============================================================
# АККАУНТЫ — ТОЛЬКО ПРЕПОДАВАТЕЛЬ
# ============================================================

@app.route("/accounts")
@teacher_required
def accounts_page():
    conn = get_db()

    search = request.args.get(
        "search",
        ""
    ).strip()

    role_filter = request.args.get(
        "role",
        ""
    ).strip()

    accounts = conn.execute("""
        SELECT
            users.id,
            users.username,
            users.role,
            users.display_name,
            users.created_at,
            users.student_id,
            students.full_name AS student_full_name,
            groups.group_name AS group_name
        FROM users
        LEFT JOIN students
            ON users.student_id = students.id
        LEFT JOIN groups
            ON students.group_id = groups.id
        ORDER BY
            CASE
                WHEN users.role = 'teacher' THEN 0
                ELSE 1
            END,
            users.display_name,
            users.username
    """).fetchall()

    if role_filter in {"teacher", "student"}:
        accounts = [
            account for account in accounts
            if account["role"] == role_filter
        ]

    if search:
        tokens = normalize_text(search).split()

        accounts = [
            account
            for account in accounts
            if all(
                token in normalize_text(
                    " ".join([
                        account["username"] or "",
                        account["display_name"] or "",
                        account["student_full_name"] or "",
                        account["group_name"] or "",
                    ])
                )
                for token in tokens
            )
        ]

    counts = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN role = 'teacher'
                    THEN 1
                    ELSE 0
                END
            ) AS teachers,
            SUM(
                CASE
                    WHEN role = 'student'
                    THEN 1
                    ELSE 0
                END
            ) AS students
        FROM users
    """).fetchone()

    conn.close()

    return render_template(
        "accounts.html",
        active_page="accounts",
        accounts=accounts,
        counts={
            "total": counts["total"] or 0,
            "teachers": counts["teachers"] or 0,
            "students": counts["students"] or 0,
        },
        filters={
            "search": search,
            "role": role_filter,
        }
    )


@app.route(
    "/accounts/<int:account_id>/delete",
    methods=["POST"]
)
@teacher_required
def delete_account(account_id):
    abort(403)


@app.route(
    "/clear/accounts/students",
    methods=["POST"]
)
@teacher_required
def clear_student_accounts():
    abort(403)


@app.route("/accounts/export.csv")
@teacher_required
def export_accounts_csv():
    conn = get_db()

    accounts = conn.execute("""
        SELECT
            users.username,
            users.role,
            users.display_name,
            users.created_at,
            students.full_name AS student_full_name,
            groups.group_name AS group_name
        FROM users
        LEFT JOIN students
            ON users.student_id = students.id
        LEFT JOIN groups
            ON students.group_id = groups.id
        ORDER BY users.role, users.display_name
    """).fetchall()

    output = io.StringIO()
    output.write("\ufeff")

    writer = csv.writer(
        output,
        delimiter=";"
    )

    writer.writerow([
        "Логин",
        "Роль",
        "Имя аккаунта",
        "Студент",
        "Группа",
        "Дата регистрации",
    ])

    for account in accounts:
        writer.writerow([
            account["username"],
            (
                "Преподаватель"
                if account["role"] == "teacher"
                else "Студент"
            ),
            account["display_name"],
            account["student_full_name"] or "",
            account["group_name"] or "",
            account["created_at"] or "",
        ])

    conn.close()

    filename = (
        "educontrol_accounts_"
        f"{date.today().isoformat()}.csv"
    )

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"'
        }
    )


# ============================================================
# ЭКСПОРТ / BACKUP — ТОЛЬКО ПРЕПОДАВАТЕЛЬ
# ============================================================

@app.route("/export.csv")
@teacher_required
def export_csv():
    update_overdue()
    conn = get_db()

    debts = get_filtered_debts(
        conn,
        search=request.args.get(
            "search",
            ""
        ).strip(),
        group=request.args.get(
            "group",
            ""
        ).strip(),
        status=request.args.get(
            "status",
            ""
        ).strip(),
        subject=request.args.get(
            "subject",
            ""
        ).strip(),
    )

    output = io.StringIO()
    output.write("\ufeff")

    writer = csv.writer(
        output,
        delimiter=";"
    )

    writer.writerow([
        "Студент",
        "Группа",
        "Преподаватель",
        "Предмет",
        "Задание",
        "Дедлайн",
        "Статус",
        "Баллы",
        "Максимум",
        "Комментарий",
    ])

    for debt in debts:
        writer.writerow([
            debt["full_name"],
            debt["group_name"],
            debt["teacher_name"] or "",
            debt["subject_name"],
            debt["assignment_name"],
            debt["repayment_deadline"] or "",
            debt["status"],
            (
                ""
                if debt["score"] is None
                else debt["score"]
            ),
            (
                ""
                if debt["max_score"] is None
                else debt["max_score"]
            ),
            debt["comment"] or "",
        ])

    conn.close()

    filename = (
        f"educontrol_report_"
        f"{date.today().isoformat()}.csv"
    )

    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"'
        }
    )


@app.route("/backup")
@teacher_required
def backup_database():
    db_path = Path(DB_NAME)

    if not db_path.exists():
        flash(
            "Файл базы данных ещё не создан.",
            "error"
        )
        return redirect(
            url_for("dashboard")
        )

    return send_file(
        db_path,
        as_attachment=True,
        download_name=(
            "educontrol_backup_"
            f"{date.today().isoformat()}.db"
        )
    )


# ============================================================
# ОШИБКИ
# ============================================================

# ============================================================
# НАВИГАЦИЯ: ПОИСКОВАЯ СТРОКА
# ============================================================

def nav_pages_for(role):
    """Разделы сайта, доступные роли. keywords — дополнительные слова для поиска."""
    if role == "teacher":
        return [
            ("Обзор", "Панель управления, статистика", url_for("dashboard"),
             "главная панель дашборд статистика"),
            ("Задолженности", "Список всех долгов", url_for("debts_page"),
             "долги задолженность пересдача просрочено"),
            ("Новая задолженность", "Добавить долг студенту", url_for("new_debt"),
             "добавить создать долг новый"),
            ("Студенты", "Список студентов по группам", url_for("students_page"),
             "студент ученики добавить студента"),
            ("Группы", "Учебные группы", url_for("groups_page"),
             "группа класс добавить группу"),
            ("Предметы", "Дисциплины", url_for("subjects_page"),
             "предмет дисциплина добавить предмет"),
            ("Преподаватели", "Список преподавателей", url_for("teachers_page"),
             "учитель преподаватель"),
            ("Аккаунты", "Учётные записи пользователей", url_for("accounts_page"),
             "аккаунт пользователь логин пароль учётка"),
            ("Резервная копия", "Скачать базу данных", url_for("backup_database"),
             "backup бэкап база скачать копия"),
            ("Экспорт задолженностей", "Скачать CSV", url_for("export_csv"),
             "csv экспорт выгрузка excel"),
            ("Выйти", "Завершить сеанс", url_for("logout"),
             "выход выйти logout"),
        ]

    return [
        ("Мой кабинет", "Мои задолженности", url_for("my_profile"),
         "профиль мои долги кабинет главная"),
        ("Преподаватели", "Список преподавателей", url_for("teachers_page"),
         "учитель преподаватель"),
        ("Выйти", "Завершить сеанс", url_for("logout"),
         "выход выйти logout"),
    ]


@app.route("/api/nav-search")
@login_required
def nav_search():
    user = current_user()
    q = normalize_text(request.args.get("q", ""))
    tokens = q.split()
    results = []

    def matches(*fields):
        haystack = normalize_text(" ".join(f or "" for f in fields))
        return all(t in haystack for t in tokens)

    # Разделы сайта (при пустом запросе показываем все — как меню)
    for title, desc, url, keywords in nav_pages_for(user["role"]):
        if not tokens or matches(title, desc, keywords):
            results.append({
                "type": "page", "title": title,
                "desc": desc, "url": url
            })

    # Преподаватели — для всех ролей (список и так виден всем)
    if tokens:
        conn = get_db()

        for t in conn.execute("""
            SELECT id, display_name FROM users
            WHERE role = 'teacher' ORDER BY display_name
        """).fetchall():
            if matches(t["display_name"]):
                results.append({
                    "type": "teacher",
                    "title": t["display_name"],
                    "desc": "Преподаватель · "
                            + ("мои долги от него"
                               if user["role"] == "student"
                               else "добавленные долги"),
                    "url": url_for("teacher_debts", teacher_id=t["id"])
                })

        conn.close()

    # Данные: только для преподавателя и только при непустом запросе
    if tokens and user["role"] == "teacher":
        conn = get_db()

        students = conn.execute("""
            SELECT students.id, students.full_name, groups.group_name
            FROM students
            JOIN groups ON students.group_id = groups.id
            ORDER BY students.full_name
        """).fetchall()

        found = 0
        for st in students:
            if matches(st["full_name"], st["group_name"]):
                results.append({
                    "type": "student",
                    "title": st["full_name"],
                    "desc": "Студент · " + st["group_name"],
                    "url": url_for("student_profile", student_id=st["id"])
                })
                found += 1
                if found >= 6:
                    break

        for g in conn.execute(
            "SELECT group_name FROM groups ORDER BY group_name"
        ).fetchall():
            if matches(g["group_name"]):
                results.append({
                    "type": "group",
                    "title": g["group_name"],
                    "desc": "Группа · студенты",
                    "url": url_for("students_page", group=g["group_name"])
                })

        for sub in conn.execute(
            "SELECT subject_name FROM subjects ORDER BY subject_name"
        ).fetchall():
            if matches(sub["subject_name"]):
                results.append({
                    "type": "subject",
                    "title": sub["subject_name"],
                    "desc": "Предмет · задолженности",
                    "url": url_for("debts_page", subject=sub["subject_name"])
                })

        conn.close()

    return jsonify(results[:20])


@app.errorhandler(403)
def forbidden(error):
    return render_template(
        "403.html"
    ), 403


@app.template_filter("num")
def num(value):
    if value is None:
        return "—"

    value = float(value)

    if value.is_integer():
        return str(int(value))

    return f"{value:.1f}"


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
