"""Shared fixtures for P2000 Monitor tests."""
import sys

import pytest

if sys.platform == "win32":
    # Windows event loops need a local socketpair; HTTP stays mocked by aioclient_mock.
    import pytest_socket

    pytest_socket.disable_socket = lambda *a, **k: None


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
