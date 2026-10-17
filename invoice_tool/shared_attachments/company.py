"""Legacy-compatible company_attachments helpers; explicit runtime dependencies."""

def get_company_attachments(*, db):
    return db().execute(
        """
        select company_attachments.*, users.name as uploader_name
        from company_attachments
        left join users on users.id = company_attachments.uploaded_by
        order by uploaded_at desc, id desc
        """
    ).fetchall()
