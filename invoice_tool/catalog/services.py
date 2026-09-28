"""Project and country catalog helpers."""

from flask import request

MRO_CANONICAL_PROJECT_NAME = "MRO Supplies配件及耗材费"


def normalized_project_name(value):
    value = str(value or "").translate({ord(char): None for char in "\u200b\u200c\u200d\ufeff"})
    return " ".join(value.strip().split())

def project_name_key(value):
    return normalized_project_name(value).casefold()

def is_mro_alias_project_name(value):
    """MRO 裸名（'MRO Supplies'、'MroSupplies'），尚未合并成规范全名的写法。"""
    return "".join(normalized_project_name(value).casefold().split()) == "mrosupplies"

def is_mro_project_name(value):
    """MRO 系列项目名：裸名或合并后的规范全名。"""
    return is_mro_alias_project_name(value) or project_name_key(value) == project_name_key(
        MRO_CANONICAL_PROJECT_NAME
    )

def build_catalog_services(*, db, current_language):
    def merge_mro_project_aliases(connection):
        canonical_name = MRO_CANONICAL_PROJECT_NAME
        def is_alias(value):
            return is_mro_alias_project_name(value)
        rows = connection.execute("select * from projects order by id").fetchall()
        for old in rows:
            if not is_alias(old["name"]):
                continue
            target = connection.execute("select id from projects where project_type = ? and name = ? order by id limit 1", (old["project_type"], canonical_name)).fetchone()
            if target:
                target_id = target["id"]
                connection.execute("update invoice_items set project_id = ? where project_id = ?", (target_id, old["id"]))
                for table in ("expense_items", "expenses"):
                    connection.execute(f"update {table} set project_id = ?, project = ? where project_id = ?", (target_id, canonical_name, old["id"]))
                connection.execute("delete from projects where id = ?", (old["id"],))
            else:
                connection.execute("update projects set name = ?, name_key = ? where id = ?", (canonical_name, project_name_key(canonical_name), old["id"]))
        for table, column in (("expense_items", "project"), ("expenses", "project"), ("invoice_items", "description")):
            for row in connection.execute(f"select id, {column} as name from {table}").fetchall():
                if is_alias(row["name"]):
                    connection.execute(f"update {table} set {column} = ? where id = ?", (canonical_name, row["id"]))

    def merge_duplicate_projects(connection):
        grouped = {}
        for row in connection.execute("select * from projects order by is_active desc, id asc").fetchall():
            normalized_name = normalized_project_name(row["name"])
            key = project_name_key(normalized_name)
            if row["name"] != normalized_name or row["name_key"] != key:
                connection.execute(
                    "update projects set name = ?, name_key = ? where id = ?",
                    (normalized_name, key, row["id"]),
                )
            grouped.setdefault((row["project_type"], key), []).append(row)
        for rows in grouped.values():
            if len(rows) < 2:
                continue
            canonical = rows[0]
            canonical_name = normalized_project_name(canonical["name"])
            duplicate_ids = [row["id"] for row in rows[1:]]
            placeholders = ",".join("?" for _ in duplicate_ids)
            connection.execute(
                f"update invoice_items set project_id = ? where project_id in ({placeholders})",
                [canonical["id"], *duplicate_ids],
            )
            connection.execute(
                f"update expense_items set project_id = ?, project = ? where project_id in ({placeholders})",
                [canonical["id"], canonical_name, *duplicate_ids],
            )
            connection.execute(
                f"update expenses set project_id = ?, project = ? where project_id in ({placeholders})",
                [canonical["id"], canonical_name, *duplicate_ids],
            )
            connection.execute(f"delete from projects where id in ({placeholders})", duplicate_ids)

    def project_name_exists(project_type, name, excluded_project_id=None):
        normalized_name_key = project_name_key(name)
        for row in db().execute("select id, name, name_key from projects where project_type = ?", (project_type,)).fetchall():
            if excluded_project_id and row["id"] == excluded_project_id:
                continue
            if (row["name_key"] or project_name_key(row["name"])) == normalized_name_key:
                return True
        return False

    def country_rows(include_inactive=False):
        active_clause = "" if include_inactive else "where countries.is_active = 1"
        language = current_language()
        return db().execute(
            f"""
            select countries.*,
                   coalesce(local.name, chinese.name, english.name, countries.code) as name,
                   coalesce(local.region_name, chinese.region_name, english.region_name, countries.region_code) as region_name
            from countries
            left join country_translations local
              on local.country_code = countries.code and local.language_code = ?
            left join country_translations chinese
              on chinese.country_code = countries.code and chinese.language_code = 'zh-CN'
            left join country_translations english
              on english.country_code = countries.code and english.language_code = 'en'
            {active_clause}
            order by countries.sort_order, name, countries.code
            """,
            (language,),
        ).fetchall()

    def country_by_code(code, include_inactive=False):
        clause = "" if include_inactive else "and is_active = 1"
        return db().execute(
            f"select * from countries where code = ? {clause}",
            ((code or "").strip().upper(),),
        ).fetchone()

    def country_translations(country_code):
        return db().execute(
            """
            select * from country_translations
            where country_code = ?
            order by case language_code when 'zh-CN' then 0 when 'en' then 1 when 'nl' then 2 else 3 end,
                     language_code
            """,
            (country_code,),
        ).fetchall()

    def country_from_form(default_code="US"):
        country = country_by_code(request.form.get("country_code") or default_code, include_inactive=True)
        if not country:
            country = country_by_code(default_code, include_inactive=True)
        return country

    return {
        "merge_mro_project_aliases": merge_mro_project_aliases,
        "merge_duplicate_projects": merge_duplicate_projects,
        "project_name_exists": project_name_exists,
        "country_rows": country_rows,
        "country_by_code": country_by_code,
        "country_translations": country_translations,
        "country_from_form": country_from_form,
    }
