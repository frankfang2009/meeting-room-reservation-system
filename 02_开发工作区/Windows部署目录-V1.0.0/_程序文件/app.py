from __future__ import annotations

import hmac
import logging
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PROJECT_DIR / "data"

START_TIMES = tuple(
    f"{minutes // 60:02d}:{minutes % 60:02d}"
    for minutes in range(8 * 60 + 30, 17 * 60 + 1, 30)
)
END_TIMES = tuple(
    f"{minutes // 60:02d}:{minutes % 60:02d}"
    for minutes in range(9 * 60, 17 * 60 + 31, 30)
)


class ReservationConflict(Exception):
    """Raised when another reservation already owns one of the requested slots."""


class ReservationUnavailable(Exception):
    """Raised when the selected room or current account changes during booking."""


def _load_or_create_secret(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    except FileNotFoundError:
        pass

    value = secrets.token_hex(32)
    _atomic_write_text(path, value)
    persisted = path.read_text(encoding="utf-8").strip()
    if not persisted:
        raise RuntimeError("无法创建系统会话密钥")
    return persisted


def _atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def create_app(test_config: Optional[dict[str, Any]] = None) -> Flask:
    app = Flask(__name__)

    supplied_config = test_config or {}
    data_dir = Path(
        supplied_config.get("DATA_DIR")
        or os.environ.get("MEETING_ROOM_DATA_DIR", DEFAULT_DATA_DIR)
    )
    data_dir.mkdir(parents=True, exist_ok=True)
    database = supplied_config.get("DATABASE", str(data_dir / "reservation.db"))
    secret_key = supplied_config.get("SECRET_KEY") or _load_or_create_secret(
        data_dir / ".secret_key"
    )
    app.config.from_mapping(
        DATABASE=database,
        SECRET_KEY=secret_key,
        INITIAL_ADMIN_PASSWORD=os.environ.get("MEETING_ROOM_INITIAL_ADMIN_PASSWORD"),
        INITIAL_CREDENTIAL_FILE=str(
            Path(database).parent / "首次登录账号密码.txt"
        ),
        SESSION_COOKIE_NAME="meeting_room_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=256 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    app.teardown_appcontext(close_db)
    _register_request_hooks(app)
    _register_template_helpers(app)
    _register_routes(app)
    _register_error_handlers(app)
    return app


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        db = sqlite3.connect(
            current_app.config["DATABASE"],
            timeout=10,
            isolation_level=None,
        )
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA synchronous = FULL")
        g.db = db
    return g.db


def close_db(_error: Optional[BaseException] = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1)),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            session_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER,
            room_name_snapshot TEXT NOT NULL,
            reserve_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            party_name TEXT NOT NULL DEFAULT '',
            case_number TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'cancelled')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE SET NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS reservation_slots (
            reservation_id INTEGER NOT NULL,
            room_id INTEGER NOT NULL,
            reserve_date TEXT NOT NULL,
            slot_time TEXT NOT NULL,
            PRIMARY KEY (room_id, reserve_date, slot_time),
            FOREIGN KEY (reservation_id) REFERENCES reservations(id) ON DELETE CASCADE,
            FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_reservations_room_date
            ON reservations(room_id, reserve_date, status);
        CREATE INDEX IF NOT EXISTS idx_reservations_user
            ON reservations(user_id, reserve_date);
        """
    )

    db.execute("BEGIN IMMEDIATE")
    try:
        seeded = db.execute(
            "SELECT value FROM app_meta WHERE key = 'initial_seed_complete'"
        ).fetchone()
        if not seeded:
            if not db.execute("SELECT id FROM users LIMIT 1").fetchone():
                initial_password = current_app.config.get("INITIAL_ADMIN_PASSWORD")
                if not initial_password:
                    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
                    initial_password = "-".join(
                        "".join(secrets.choice(alphabet) for _ in range(4))
                        for _ in range(3)
                    )
                credential_file = Path(
                    current_app.config["INITIAL_CREDENTIAL_FILE"]
                )
                _atomic_write_text(
                    credential_file,
                    "会议室预约系统首次登录账号\n"
                    "==========================\n\n"
                    "用户名：admin\n"
                    f"密码：{initial_password}\n\n"
                    "登录后请立即在“管理后台 → 用户管理”中修改密码。\n"
                    "修改成功后，这个初始密码会自动失效。\n",
                )
                db.execute(
                    "INSERT INTO users (username, password_hash, display_name, is_admin) "
                    "VALUES (?, ?, ?, 1)",
                    ("admin", generate_password_hash(initial_password), "管理员"),
                )
            if not db.execute("SELECT id FROM rooms LIMIT 1").fetchone():
                for order, name in enumerate(("笔录室1", "笔录室2", "笔录室3"), 1):
                    db.execute(
                        "INSERT INTO rooms (name, sort_order) VALUES (?, ?)",
                        (name, order),
                    )
            db.execute(
                "INSERT INTO app_meta (key, value) VALUES ('initial_seed_complete', '1')"
            )
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def _register_request_hooks(app: Flask) -> None:
    @app.before_request
    def protect_post_requests() -> None:
        if request.method != "POST":
            return
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("_csrf_token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            abort(400, description="页面已过期，请返回后重新操作。")

    @app.after_request
    def add_security_headers(response: Any) -> Any:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Meeting-Room-System"] = "1"
        return response


def csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def _current_user() -> Optional[dict[str, Any]]:
    if "current_user" in g:
        return g.current_user

    user_id = session.get("user_id")
    if not user_id:
        g.current_user = None
        return None

    row = get_db().execute(
        "SELECT * FROM users WHERE id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    if row is None or row["session_version"] != session.get("session_version"):
        session.clear()
        g.current_user = None
        return None

    g.current_user = dict(row)
    return g.current_user


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if _current_user() is None:
            flash("请先登录", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user = _current_user()
        if user is None:
            flash("请先登录", "warning")
            return redirect(url_for("login"))
        if not user["is_admin"]:
            flash("需要管理员权限", "danger")
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped


def admin_write_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Authorize an administrator again after obtaining the database write lock."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        user = _current_user()
        if user is None:
            flash("请先登录", "warning")
            return redirect(url_for("login"))
        if not user["is_admin"]:
            flash("需要管理员权限", "danger")
            return redirect(url_for("index"))

        db = get_db()
        db.execute("BEGIN IMMEDIATE")
        locked_user = db.execute(
            """
            SELECT id FROM users
            WHERE id = ? AND is_active = 1 AND is_admin = 1
              AND session_version = ?
            """,
            (user["id"], user["session_version"]),
        ).fetchone()
        if locked_user is None:
            db.execute("ROLLBACK")
            session.clear()
            g.current_user = None
            flash("账号权限已改变，请重新登录", "warning")
            return redirect(url_for("login"))

        try:
            response = view(*args, **kwargs)
            if db.in_transaction:
                db.execute("COMMIT")
            callbacks = g.pop("after_admin_commit", [])
            for callback in callbacks:
                try:
                    callback()
                except OSError:
                    logging.exception("提交后的本地文件清理失败")
            return response
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise

    return wrapped


def _register_template_helpers(app: Flask) -> None:
    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        return {
            "current_user": _current_user(),
            "csrf_token": csrf_token,
        }


def _time_to_minutes(value: str) -> int:
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def _reservation_slot_times(start_time: str, end_time: str) -> list[str]:
    start = _time_to_minutes(start_time)
    end = _time_to_minutes(end_time)
    return [
        f"{minutes // 60:02d}:{minutes % 60:02d}"
        for minutes in range(start, end, 30)
    ]


def _parse_date(value: str) -> Optional[str]:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _limited_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _room_reservations(room_id: int, target_date: str) -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT r.*, u.display_name
        FROM reservations r
        JOIN users u ON u.id = r.user_id
        WHERE r.room_id = ? AND r.reserve_date = ? AND r.status = 'pending'
        ORDER BY r.start_time
        """,
        (room_id, target_date),
    ).fetchall()
    return [dict(row) for row in rows]


def _build_slot_map(reservations: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for reservation in reservations:
        for slot_time in _reservation_slot_times(
            reservation["start_time"], reservation["end_time"]
        ):
            result.setdefault(slot_time, reservation)
    return result


def _active_reservation_filter() -> tuple[str, tuple[str, str, str]]:
    now = datetime.now()
    return (
        "(reserve_date > ? OR (reserve_date = ? AND end_time > ?))",
        (now.date().isoformat(), now.date().isoformat(), now.strftime("%H:%M")),
    )


def _reservation_display_status(reservation: dict[str, Any], now: datetime) -> str:
    if reservation["status"] == "cancelled":
        return "已取消"
    end_at = datetime.strptime(
        f"{reservation['reserve_date']} {reservation['end_time']}",
        "%Y-%m-%d %H:%M",
    )
    return "已完成" if end_at < now else "待使用"


def _register_routes(app: Flask) -> None:
    @app.route("/login", methods=["GET", "POST"])
    def login() -> Any:
        if request.method == "POST":
            username = _limited_text(request.form.get("username"), 80)
            password = str(request.form.get("password", ""))[:256]
            user = get_db().execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1",
                (username,),
            ).fetchone()
            if user and check_password_hash(user["password_hash"], password):
                session.clear()
                session["user_id"] = user["id"]
                session["session_version"] = user["session_version"]
                csrf_token()
                flash("登录成功", "success")
                return redirect(url_for("index"))
            flash("用户名或密码错误", "danger")
        return render_template("login.html")

    @app.route("/logout")
    def logout() -> Any:
        session.clear()
        flash("已退出登录", "info")
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index() -> Any:
        requested_date = request.args.get("date", "")
        current_date = _parse_date(requested_date) or date.today().isoformat()
        current_date_obj = datetime.strptime(current_date, "%Y-%m-%d").date()

        rooms = [
            dict(row)
            for row in get_db()
            .execute(
                "SELECT * FROM rooms WHERE is_active = 1 ORDER BY sort_order, id"
            )
            .fetchall()
        ]
        for room in rooms:
            reservations = _room_reservations(room["id"], current_date)
            room["slot_map"] = _build_slot_map(reservations)

        return render_template(
            "index.html",
            current_date=current_date_obj,
            prev_date=(
                current_date_obj - timedelta(days=1)
                if current_date_obj > date.min
                else current_date_obj
            ),
            next_date=(
                current_date_obj + timedelta(days=1)
                if current_date_obj < date.max
                else current_date_obj
            ),
            today=date.today(),
            rooms=rooms,
            time_slots=START_TIMES,
        )

    @app.route("/reserve", methods=["GET", "POST"])
    @login_required
    def reserve() -> Any:
        db = get_db()
        rooms = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM rooms WHERE is_active = 1 ORDER BY sort_order, id"
            ).fetchall()
        ]

        if request.method == "POST":
            room_id = _parse_int(request.form.get("room_id"))
            reserve_date = _parse_date(request.form.get("reserve_date", ""))
            start_time = request.form.get("start_time", "")
            end_time = request.form.get("end_time", "")
            party_name = _limited_text(request.form.get("party_name"), 120)
            case_number = _limited_text(request.form.get("case_number"), 120)
            notes = _limited_text(request.form.get("notes"), 500)

            def render_error(message: str) -> Any:
                flash(message, "danger")
                return render_template(
                    "reserve.html",
                    rooms=rooms,
                    room_id=room_id,
                    reserve_date=reserve_date or request.form.get("reserve_date", ""),
                    start_time=start_time,
                    end_time=end_time,
                    start_times=START_TIMES,
                    end_times=END_TIMES,
                    party_name=party_name,
                    case_number=case_number,
                    notes=notes,
                )

            room = None
            if room_id is not None:
                room = db.execute(
                    "SELECT * FROM rooms WHERE id = ? AND is_active = 1",
                    (room_id,),
                ).fetchone()
            if room is None:
                return render_error("请选择可用的会议室")
            if reserve_date is None:
                return render_error("请选择正确的预约日期")
            if start_time not in START_TIMES or end_time not in END_TIMES:
                return render_error("请选择正确的预约时间")
            if _time_to_minutes(start_time) >= _time_to_minutes(end_time):
                return render_error("结束时间必须晚于开始时间")

            slots = _reservation_slot_times(start_time, end_time)
            current_user = _current_user()
            before_write = current_app.config.get("BEFORE_RESERVATION_WRITE")
            if before_write:
                before_write()
            try:
                db.execute("BEGIN IMMEDIATE")
                locked_user = db.execute(
                    """
                    SELECT id FROM users
                    WHERE id = ? AND is_active = 1 AND session_version = ?
                    """,
                    (current_user["id"], current_user["session_version"]),
                ).fetchone()
                locked_room = db.execute(
                    "SELECT * FROM rooms WHERE id = ? AND is_active = 1",
                    (room_id,),
                ).fetchone()
                if locked_user is None or locked_room is None:
                    raise ReservationUnavailable

                placeholders = ",".join("?" for _ in slots)
                conflict = db.execute(
                    f"""
                    SELECT 1 FROM reservation_slots
                    WHERE room_id = ? AND reserve_date = ?
                      AND slot_time IN ({placeholders})
                    LIMIT 1
                    """,
                    (room_id, reserve_date, *slots),
                ).fetchone()
                if conflict:
                    raise ReservationConflict

                cursor = db.execute(
                    """
                    INSERT INTO reservations (
                        room_id, room_name_snapshot, reserve_date, start_time,
                        end_time, user_id, party_name, case_number, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        room_id,
                        locked_room["name"],
                        reserve_date,
                        start_time,
                        end_time,
                        locked_user["id"],
                        party_name,
                        case_number,
                        notes,
                    ),
                )
                db.executemany(
                    """
                    INSERT INTO reservation_slots
                        (reservation_id, room_id, reserve_date, slot_time)
                    VALUES (?, ?, ?, ?)
                    """,
                    [
                        (cursor.lastrowid, room_id, reserve_date, slot_time)
                        for slot_time in slots
                    ],
                )
                db.execute("COMMIT")
            except ReservationUnavailable:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                g.pop("current_user", None)
                if _current_user() is None:
                    flash("账号状态已改变，请重新登录", "warning")
                    return redirect(url_for("login"))
                return render_error("会议室状态已改变，请重新选择")
            except (ReservationConflict, sqlite3.IntegrityError):
                if db.in_transaction:
                    db.execute("ROLLBACK")
                return render_error("该时段已被预约，请选择其他时段")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

            flash("预约成功！", "success")
            return redirect(url_for("index", date=reserve_date))

        selected_room_id = _parse_int(
            request.args.get("room_id"), rooms[0]["id"] if rooms else 1
        )
        selected_date = _parse_date(request.args.get("date", "")) or date.today().isoformat()
        start_time = request.args.get("start_time", "09:00")
        if start_time not in START_TIMES:
            start_time = "09:00"
        default_end_index = min(START_TIMES.index(start_time), len(END_TIMES) - 1)
        default_end = END_TIMES[default_end_index]
        end_time = request.args.get("end_time", default_end)
        if end_time not in END_TIMES:
            end_time = default_end

        return render_template(
            "reserve.html",
            rooms=rooms,
            room_id=selected_room_id,
            reserve_date=selected_date,
            start_time=start_time,
            end_time=end_time,
            start_times=START_TIMES,
            end_times=END_TIMES,
            party_name=request.args.get("party_name", "")[:120],
            case_number=request.args.get("case_number", "")[:120],
            notes=request.args.get("notes", "")[:500],
        )

    @app.post("/cancel/<int:res_id>")
    @login_required
    def cancel_reservation(res_id: int) -> Any:
        db = get_db()
        user_snapshot = _current_user()
        db.execute("BEGIN IMMEDIATE")
        try:
            user = db.execute(
                """
                SELECT * FROM users
                WHERE id = ? AND is_active = 1 AND session_version = ?
                """,
                (user_snapshot["id"], user_snapshot["session_version"]),
            ).fetchone()
            if user is None:
                db.execute("ROLLBACK")
                session.clear()
                g.current_user = None
                flash("账号状态已改变，请重新登录", "warning")
                return redirect(url_for("login"))
            reservation = db.execute(
                "SELECT * FROM reservations WHERE id = ?", (res_id,)
            ).fetchone()
            if reservation is None:
                db.execute("ROLLBACK")
                flash("预约不存在", "danger")
                return redirect(url_for("my_reservations"))
            if reservation["user_id"] != user["id"] and not user["is_admin"]:
                db.execute("ROLLBACK")
                flash("无权取消此预约", "danger")
                return redirect(url_for("index"))

            end_at = datetime.strptime(
                f"{reservation['reserve_date']} {reservation['end_time']}",
                "%Y-%m-%d %H:%M",
            )
            if end_at < datetime.now() and not user["is_admin"]:
                db.execute("ROLLBACK")
                flash("已过期的预约无法取消", "warning")
                return redirect(url_for("my_reservations"))

            db.execute(
                "UPDATE reservations SET status = 'cancelled' WHERE id = ?",
                (res_id,),
            )
            db.execute(
                "DELETE FROM reservation_slots WHERE reservation_id = ?", (res_id,)
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        flash("已取消预约", "info")
        return redirect(url_for("my_reservations"))

    @app.route("/my")
    @login_required
    def my_reservations() -> Any:
        rows = get_db().execute(
            """
            SELECT r.*, COALESCE(rm.name, r.room_name_snapshot) AS room_name,
                   u.display_name
            FROM reservations r
            JOIN users u ON u.id = r.user_id
            LEFT JOIN rooms rm ON rm.id = r.room_id
            WHERE r.user_id = ?
            ORDER BY r.reserve_date DESC, r.start_time DESC
            """,
            (_current_user()["id"],),
        ).fetchall()
        now = datetime.now()
        reservations = []
        for row in rows:
            item = dict(row)
            item["display_status"] = _reservation_display_status(item, now)
            reservations.append(item)
        return render_template("my_reservations.html", reservations=reservations)

    @app.route("/admin")
    @admin_required
    def admin() -> Any:
        return redirect(url_for("admin_reservations"))

    @app.route("/admin/reservations")
    @admin_required
    def admin_reservations() -> Any:
        default_start = (date.today() - timedelta(days=7)).isoformat()
        default_end = (date.today() + timedelta(days=30)).isoformat()
        start_date = _parse_date(request.args.get("start_date", "")) or default_start
        end_date = _parse_date(request.args.get("end_date", "")) or default_end
        rows = get_db().execute(
            """
            SELECT r.*, u.display_name, u.username,
                   COALESCE(rm.name, r.room_name_snapshot) AS room_name
            FROM reservations r
            JOIN users u ON u.id = r.user_id
            LEFT JOIN rooms rm ON rm.id = r.room_id
            WHERE r.reserve_date BETWEEN ? AND ?
            ORDER BY r.reserve_date DESC, r.start_time DESC
            """,
            (start_date, end_date),
        ).fetchall()
        reservations = [dict(row) for row in rows]
        now = datetime.now()
        for reservation in reservations:
            reservation["display_status"] = _reservation_display_status(
                reservation, now
            )
        return render_template(
            "admin_reservations.html",
            reservations=reservations,
            start_date=start_date,
            end_date=end_date,
        )

    @app.route("/admin/users")
    @admin_required
    def admin_users() -> Any:
        users = [
            dict(row)
            for row in get_db()
            .execute("SELECT * FROM users ORDER BY id")
            .fetchall()
        ]
        return render_template("admin_users.html", users=users)

    @app.post("/admin/users/add")
    @admin_write_required
    def admin_add_user() -> Any:
        username = _limited_text(request.form.get("username"), 80)
        password = str(request.form.get("password", ""))[:256]
        display_name = _limited_text(request.form.get("display_name"), 80)
        is_admin = 1 if request.form.get("is_admin") else 0
        if not username or not password or not display_name:
            flash("请填写完整信息", "danger")
            return redirect(url_for("admin_users"))
        try:
            get_db().execute(
                """
                INSERT INTO users (username, password_hash, display_name, is_admin)
                VALUES (?, ?, ?, ?)
                """,
                (username, generate_password_hash(password), display_name, is_admin),
            )
        except sqlite3.IntegrityError:
            flash("用户名已存在", "danger")
            return redirect(url_for("admin_users"))
        flash("用户添加成功", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/edit/<int:uid>")
    @admin_write_required
    def admin_edit_user(uid: int) -> Any:
        db = get_db()
        username = _limited_text(request.form.get("username"), 80)
        display_name = _limited_text(request.form.get("display_name"), 80)
        new_password = str(request.form.get("new_password") or "").strip()[:256]
        is_admin = 1 if request.form.get("is_admin") else 0
        is_active = 1 if request.form.get("is_active") else 0
        if not username or not display_name:
            flash("用户名和显示名不能为空", "danger")
            return redirect(url_for("admin_users"))
        current_target = db.execute(
            "SELECT * FROM users WHERE id = ?", (uid,)
        ).fetchone()
        if current_target is None:
            flash("用户不存在", "danger")
            return redirect(url_for("admin_users"))
        if db.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?",
            (username, uid),
        ).fetchone():
            flash("用户名已存在", "danger")
            return redirect(url_for("admin_users"))
        actor_id = _current_user()["id"]
        if uid == actor_id and not is_active:
            flash("不能停用当前登录账号", "danger")
            return redirect(url_for("admin_users"))
        if current_target["is_admin"] and current_target["is_active"] and (
            not is_admin or not is_active
        ):
            active_admins = db.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1"
            ).fetchone()[0]
            if active_admins <= 1:
                flash("至少需要保留一名启用的管理员", "danger")
                return redirect(url_for("admin_users"))

        revoke_sessions = bool(
            new_password
            or current_target["is_admin"] != is_admin
            or current_target["is_active"] != is_active
        )
        if new_password:
            db.execute(
                """
                UPDATE users
                SET username = ?, display_name = ?, is_admin = ?, is_active = ?,
                    password_hash = ?, session_version = session_version + ?
                WHERE id = ?
                """,
                (
                    username,
                    display_name,
                    is_admin,
                    is_active,
                    generate_password_hash(new_password),
                    1 if revoke_sessions else 0,
                    uid,
                ),
            )
            if uid == 1:
                credential_file = Path(
                    current_app.config["INITIAL_CREDENTIAL_FILE"]
                )

                def remove_initial_credential() -> None:
                    try:
                        credential_file.unlink()
                    except FileNotFoundError:
                        pass

                g.setdefault("after_admin_commit", []).append(
                    remove_initial_credential
                )
        else:
            db.execute(
                """
                UPDATE users
                SET username = ?, display_name = ?, is_admin = ?, is_active = ?,
                    session_version = session_version + ?
                WHERE id = ?
                """,
                (
                    username,
                    display_name,
                    is_admin,
                    is_active,
                    1 if revoke_sessions else 0,
                    uid,
                ),
            )
        if uid == actor_id and revoke_sessions:
            session.clear()
            g.current_user = None
            message = (
                "密码已修改，请使用新密码重新登录"
                if new_password
                else "账号权限已修改，请重新登录"
            )
            flash(message, "success")
            return redirect(url_for("login"))
        g.pop("current_user", None)
        flash("用户信息已更新", "success")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/delete/<int:uid>")
    @admin_write_required
    def admin_delete_user(uid: int) -> Any:
        if uid == _current_user()["id"]:
            flash("不能禁用自己", "danger")
            return redirect(url_for("admin_users"))
        db = get_db()
        target = db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if target is None:
            flash("用户不存在", "info")
            return redirect(url_for("admin_users"))
        if target["is_admin"] and target["is_active"]:
            active_admins = db.execute(
                "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1"
            ).fetchone()[0]
            if active_admins <= 1:
                flash("至少需要保留一名启用的管理员", "danger")
                return redirect(url_for("admin_users"))
        cursor = db.execute(
            """
            UPDATE users
            SET is_active = 0, session_version = session_version + 1
            WHERE id = ?
            """,
            (uid,),
        )
        flash("用户已禁用" if cursor.rowcount else "用户不存在", "info")
        return redirect(url_for("admin_users"))

    @app.route("/admin/rooms")
    @admin_required
    def admin_rooms() -> Any:
        db = get_db()
        rooms = [
            dict(row)
            for row in db.execute("SELECT * FROM rooms ORDER BY sort_order, id").fetchall()
        ]
        active_sql, active_params = _active_reservation_filter()
        for room in rooms:
            room["reservation_count"] = db.execute(
                f"""
                SELECT COUNT(*) FROM reservations
                WHERE room_id = ? AND status = 'pending' AND {active_sql}
                """,
                (room["id"], *active_params),
            ).fetchone()[0]
        return render_template("admin_rooms.html", rooms=rooms)

    @app.post("/admin/rooms/add")
    @admin_write_required
    def admin_add_room() -> Any:
        name = _limited_text(request.form.get("name"), 80)
        sort_order = _parse_int(request.form.get("sort_order"), 0)
        if not name:
            flash("会议室名称不能为空", "danger")
            return redirect(url_for("admin_rooms"))
        try:
            get_db().execute(
                "INSERT INTO rooms (name, sort_order) VALUES (?, ?)",
                (name, sort_order),
            )
        except sqlite3.IntegrityError:
            flash("会议室名称已存在", "danger")
            return redirect(url_for("admin_rooms"))
        flash("会议室添加成功", "success")
        return redirect(url_for("admin_rooms"))

    @app.post("/admin/rooms/edit/<int:rid>")
    @admin_write_required
    def admin_edit_room(rid: int) -> Any:
        db = get_db()
        name = _limited_text(request.form.get("name"), 80)
        sort_order = _parse_int(request.form.get("sort_order"), 0)
        is_active = 1 if request.form.get("is_active") else 0
        if not name:
            flash("会议室名称不能为空", "danger")
            return redirect(url_for("admin_rooms"))
        if not db.execute(
            "SELECT id FROM rooms WHERE id = ?", (rid,)
        ).fetchone():
            flash("会议室不存在", "danger")
            return redirect(url_for("admin_rooms"))
        if db.execute(
            "SELECT id FROM rooms WHERE name = ? AND id != ?", (name, rid)
        ).fetchone():
            flash("会议室名称已存在", "danger")
            return redirect(url_for("admin_rooms"))
        db.execute(
            "UPDATE rooms SET name = ?, sort_order = ?, is_active = ? WHERE id = ?",
            (name, sort_order, is_active, rid),
        )
        db.execute(
            "UPDATE reservations SET room_name_snapshot = ? WHERE room_id = ?",
            (name, rid),
        )
        flash("会议室信息已更新", "success")
        return redirect(url_for("admin_rooms"))

    @app.post("/admin/rooms/delete/<int:rid>")
    @admin_write_required
    def admin_delete_room(rid: int) -> Any:
        db = get_db()
        active_sql, active_params = _active_reservation_filter()
        room = db.execute(
            "SELECT name FROM rooms WHERE id = ?", (rid,)
        ).fetchone()
        if room is None:
            flash("会议室不存在", "info")
            return redirect(url_for("admin_rooms"))
        count = db.execute(
            f"""
            SELECT COUNT(*) FROM reservations
            WHERE room_id = ? AND status = 'pending' AND {active_sql}
            """,
            (rid, *active_params),
        ).fetchone()[0]
        if count:
            flash("该会议室有未完成的预约，无法删除", "danger")
            return redirect(url_for("admin_rooms"))
        db.execute(
            "UPDATE reservations SET room_name_snapshot = ? WHERE room_id = ?",
            (room["name"], rid),
        )
        cursor = db.execute("DELETE FROM rooms WHERE id = ?", (rid,))
        flash("会议室已删除" if cursor.rowcount else "会议室不存在", "info")
        return redirect(url_for("admin_rooms"))

    @app.post("/admin/cancel/<int:res_id>")
    @admin_write_required
    def admin_cancel_reservation(res_id: int) -> Any:
        db = get_db()
        active_sql, active_params = _active_reservation_filter()
        cursor = db.execute(
            f"""
            UPDATE reservations SET status = 'cancelled'
            WHERE id = ? AND status = 'pending' AND {active_sql}
            """,
            (res_id, *active_params),
        )
        if cursor.rowcount:
            db.execute(
                "DELETE FROM reservation_slots WHERE reservation_id = ?", (res_id,)
            )
            flash("预约已取消", "info")
        else:
            flash("预约已结束、已取消或不存在", "warning")
        start_date = _parse_date(request.form.get("start_date", ""))
        end_date = _parse_date(request.form.get("end_date", ""))
        if start_date and end_date:
            return redirect(
                url_for(
                    "admin_reservations",
                    start_date=start_date,
                    end_date=end_date,
                )
            )
        return redirect(url_for("admin_reservations"))


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(error: Any) -> tuple[str, int]:
        return (
            render_template(
                "error.html",
                title="操作没有完成",
                message=getattr(error, "description", "请求内容不正确。"),
            ),
            400,
        )

    @app.errorhandler(404)
    def not_found(_error: Any) -> tuple[str, int]:
        return (
            render_template(
                "error.html", title="页面不存在", message="请返回系统首页继续操作。"
            ),
            404,
        )


app = create_app()


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="127.0.0.1", port=8080, debug=False)
