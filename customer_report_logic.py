CUSTOMER_REPORT_MIGRATION_KEY = "service_report_customer_report_v1"


def normalize_customer_report_choice(choice, writer_value):
    normalized_choice = (choice or "").strip().lower()
    normalized_writer = (writer_value or "").strip()
    if not normalized_choice:
        normalized_choice = "yes" if normalized_writer else "no"
    if normalized_choice not in {"yes", "no"}:
        raise ValueError("请选择是否填写客户日报。")
    if normalized_choice == "no":
        return "no", ""
    if not normalized_writer:
        raise ValueError("选择填写客户日报后，必须选择一位填写员工。")
    return "yes", normalized_writer


def migrate_historical_customer_reports(connection, migrated_at, logger=None):
    marker = connection.execute(
        "select value from settings where key = ?",
        (CUSTOMER_REPORT_MIGRATION_KEY,),
    ).fetchone()
    if marker:
        return False

    report_count = connection.execute(
        "select count(*) as count from service_reports"
    ).fetchone()["count"]
    if report_count == 0:
        connection.execute(
            "insert into settings (key, value) values (?, ?)",
            (CUSTOMER_REPORT_MIGRATION_KEY, migrated_at),
        )
        return True

    gaoyang = connection.execute(
        """
        select id from users
        where trim(name) = '高阳'
           or lower(trim(email)) = 'gaoyangproduction@gmail.com'
        order by case when trim(name) = '高阳' then 0 else 1 end, id
        limit 1
        """
    ).fetchone()
    if not gaoyang:
        if logger:
            logger.warning("Historical customer report migration is waiting for employee 高阳.")
        return False

    connection.execute(
        """
        update service_reports
        set report_writer_id = case
            when exists (
                select 1
                from service_orders
                left join manufacturers
                  on manufacturers.id = service_orders.manufacturer_id
                left join buyers on buyers.id = service_orders.buyer_id
                where service_orders.id = service_reports.service_order_id
                  and lower(coalesce(
                      nullif(trim(manufacturers.name), ''),
                      nullif(trim(buyers.equipment_manufacturer), ''),
                      ''
                  )) = lower('阳光')
            ) then ?
            else null
        end
        """,
        (gaoyang["id"],),
    )
    connection.execute(
        "insert into settings (key, value) values (?, ?)",
        (CUSTOMER_REPORT_MIGRATION_KEY, migrated_at),
    )
    return True
