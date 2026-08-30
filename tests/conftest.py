"""Test environment.

`web.config.settings` is built at import time, so these have to be set before
any test module imports anything under `web.`. A conftest is imported before
the test modules in its directory, which is the only place that reliably
happens — setting them at the top of a single test module makes the values
depend on which module pytest imports first.
"""

import os

os.environ.setdefault("AUTH_MODE", "dev")
os.environ.setdefault("DEV_USER", "thomas")
os.environ.setdefault("DEV_USER_NAME", "Thomas V")
os.environ.setdefault("SESSION_SECRET", "test-secret")


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: needs a live DATABASE_URI to run"
    )


def pytest_collection_modifyitems(config, items):
    """Skip the integration tests when there is no database to talk to.

    They previously errored with KeyError: 'DATABASE_URI' on every run, which
    made a clean suite look like 13 failures and buried real ones.
    """
    import pytest

    if os.environ.get("DATABASE_URI"):
        return
    skip = pytest.mark.skip(reason="needs a live DATABASE_URI (set it to run)")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
