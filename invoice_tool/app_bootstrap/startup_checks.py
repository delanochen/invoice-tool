"""Legacy-compatible residual_bootstrap helpers; explicit runtime dependencies."""

def verify_data_directory_identity(*, DATA_DIRECTORY_IDENTITY, DATA_IDENTITY_FILE, PostgreSQLConnection, REQUIRE_DATA_DIRECTORY_IDENTITY):
    if not REQUIRE_DATA_DIRECTORY_IDENTITY:
        return
    if not DATA_DIRECTORY_IDENTITY:
        raise RuntimeError("DATA_DIRECTORY_IDENTITY is required in protected deployments.")
    try:
        with open(DATA_IDENTITY_FILE, "r", encoding="utf-8") as identity_file:
            actual_identity = identity_file.read().strip()
    except FileNotFoundError as error:
        raise RuntimeError(
            "Refusing to start: the mounted data directory has no identity marker."
        ) from error
    if actual_identity != DATA_DIRECTORY_IDENTITY:
        raise RuntimeError(
            "Refusing to start: the mounted data directory identity does not match."
        )
    identity_db = PostgreSQLConnection()
    try:
        identity_row = identity_db.execute(
            "select value from settings where key = 'production_database_identity'"
        ).fetchone()
    finally:
        identity_db.close()
    if not identity_row or identity_row[0] != DATA_DIRECTORY_IDENTITY:
        raise RuntimeError("Protected PostgreSQL database identity mismatch.")


def ensure_postgres_admin(*, IntegrityError, app, db, generate_password_hash, now, os):
    """PostgreSQL 模式下沿用 init_db 的管理员引导语义（fail-closed）。

    库里已有管理员时是空操作（每次启动一次 SELECT）；全新 PG 部署或灾备
    恢复后没有管理员时，按 ADMIN_EMAIL / ADMIN_PASSWORD 引导，凭据缺失、
    为空、为默认密码、或邮箱已属于普通用户时拒绝启动。
    """
    with app.app_context():
        connection = db()
        if connection.execute(
            "select id from users where role = 'admin' limit 1"
        ).fetchone():
            return
        admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if not admin_email:
            raise RuntimeError(
                "Refusing to initialize administrator: ADMIN_EMAIL is required."
            )
        if not admin_password:
            raise RuntimeError(
                "Refusing to initialize administrator: ADMIN_PASSWORD is required."
            )
        if admin_password == "change-me-now":
            raise RuntimeError(
                "Refusing to initialize administrator: a secure ADMIN_PASSWORD is required."
            )
        if connection.execute(
            "select id from users where lower(email) = ? limit 1",
            (admin_email,),
        ).fetchone():
            raise RuntimeError(
                "Refusing to initialize administrator: ADMIN_EMAIL already belongs to an existing user."
            )
        try:
            connection.execute(
                """
                insert into users (name, email, password_hash, role, created_at)
                values (?, ?, ?, 'admin', ?)
                """,
                ("Admin", admin_email, generate_password_hash(admin_password), now()),
            )
            connection.commit()
        except IntegrityError:
            # 多 worker 并发启动：另一个进程已插入管理员，视为成功
            if connection.execute(
                "select id from users where role = 'admin' limit 1"
            ).fetchone():
                return
            raise


def init_db(*, ensure_postgres_admin, verify_postgres_schema):
    """Validate the migrated PostgreSQL schema and bootstrap its first admin."""
    verify_postgres_schema()
    ensure_postgres_admin()
    return
