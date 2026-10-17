"""Legacy-compatible image_detection helpers; explicit runtime dependencies."""

def is_image_attachment(content_type, filename, *, ALLOWED_IMAGE_EXTENSIONS):
    if (content_type or "").strip().casefold().startswith("image/"):
        return True
    name = (filename or "").strip().casefold()
    return "." in name and name.rsplit(".", 1)[1] in ALLOWED_IMAGE_EXTENSIONS


def is_customer_reimbursement_image_attachment(attachment, *, customer_reimbursement_attachment_path, is_image_attachment, valid_image_file):
    if is_image_attachment(attachment["content_type"], attachment["original_filename"]):
        return True
    return valid_image_file(customer_reimbursement_attachment_path(attachment))
