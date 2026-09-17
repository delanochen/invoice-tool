"""Pytest shared configuration.

The app refuses to initialize its schema without an administrator
(ADMIN_EMAIL / ADMIN_PASSWORD). Tests run against throwaway databases, so
provide a per-session random password here instead of asking every test
file (or every CI command line) to embed credentials.
"""
import os
import secrets

os.environ.setdefault("ADMIN_EMAIL", "pytest-admin@example.invalid")
os.environ.setdefault("ADMIN_PASSWORD", "pytest-" + secrets.token_urlsafe(24))
