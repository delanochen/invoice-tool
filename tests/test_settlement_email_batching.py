from invoice_tool.customer_reimbursements.documents import _split_email_attachment_batches


def attachment(name, size):
    return {"filename": name, "content": b"x" * size, "maintype": "application", "subtype": "octet-stream"}


def test_settlement_email_attachments_split_before_gmail_limit():
    mib = 1024 * 1024
    batches = _split_email_attachment_batches([
        attachment("statement.pdf", 4 * mib),
        attachment("support-a.pdf", 10 * mib),
        attachment("support-b.pdf", 8 * mib),
    ])
    assert [[item["filename"] for item in batch] for batch in batches] == [
        ["statement.pdf", "support-a.pdf"],
        ["support-b.pdf"],
    ]


def test_single_oversized_settlement_attachment_is_rejected_before_send():
    mib = 1024 * 1024
    try:
        _split_email_attachment_batches([attachment("too-large.pdf", 18 * mib)])
    except ValueError as error:
        assert "too-large.pdf" in str(error)
        assert "17 MB" in str(error)
    else:
        raise AssertionError("expected oversized attachment validation")
