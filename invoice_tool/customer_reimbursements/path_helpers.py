"""Legacy-compatible residual_settlement_paths helpers; explicit runtime dependencies."""

def customer_reimbursement_dir(reimbursement_id, *, CUSTOMER_REIMBURSEMENT_DIR, os):
    path = os.path.join(CUSTOMER_REIMBURSEMENT_DIR, str(reimbursement_id))
    os.makedirs(path, exist_ok=True)
    return path


def customer_reimbursement_attachment_dir(reimbursement_id, *, customer_reimbursement_dir, os):
    path = os.path.join(customer_reimbursement_dir(reimbursement_id), "attachments")
    os.makedirs(path, exist_ok=True)
    return path


def customer_reimbursement_file_path(reimbursement, *, CUSTOMER_REIMBURSEMENT_DIR, os):
    return os.path.join(CUSTOMER_REIMBURSEMENT_DIR, str(reimbursement["id"]), reimbursement["stored_filename"])


def customer_reimbursement_attachment_path(attachment, *, CUSTOMER_REIMBURSEMENT_DIR, os):
    return os.path.join(CUSTOMER_REIMBURSEMENT_DIR, str(attachment["customer_reimbursement_id"]), "attachments", attachment["stored_filename"])


def save_customer_reimbursement_uploads(reimbursement_id, *, save_customer_reimbursement_attachment, uploaded_attachments_from_request):
    for uploaded in uploaded_attachments_from_request():
        save_customer_reimbursement_attachment(reimbursement_id, uploaded)
