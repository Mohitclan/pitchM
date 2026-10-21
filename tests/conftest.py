"""Tests run with the default settings, whatever is in the developer's .env."""
import os

# Must be set before backend.main is imported (it loads .env, which never overrides existing variables).
os.environ["AUTH_MODE"] = "off"
os.environ.pop("ALLOWED_EMAIL_DOMAINS", None)
os.environ.pop("ADMIN_EMAILS", None)
