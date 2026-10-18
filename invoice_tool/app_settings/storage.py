"""Legacy-compatible setting_write helpers; explicit runtime dependencies."""

def get_setting(key, default="", *, db):
    row = db().execute("select value from settings where key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value, *, db):
    db().execute(
        """
        insert into settings (key, value) values (?, ?)
        on conflict(key) do update set value = excluded.value
        """,
        (key, value),
    )
