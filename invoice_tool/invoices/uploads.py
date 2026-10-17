"""Legacy-compatible invoice_uploads helpers; explicit runtime dependencies."""

def uploaded_attachments_from_request(*, request):
    return [
        file
        for file in request.files.getlist("attachments") + request.files.getlist("attachment")
        if file and file.filename
    ]
