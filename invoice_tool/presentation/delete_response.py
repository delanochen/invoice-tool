"""Support local list deletion while keeping ordinary form redirects."""
from flask import flash, jsonify, redirect, request, url_for


def delete_response(endpoint, message, *, success):
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(ok=success, message=message), 200 if success else 409
    flash(message, 'success' if success else 'error')
    return redirect(url_for(endpoint))
