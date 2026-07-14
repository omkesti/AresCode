"""Toy authentication module for the agent test-fixture repo."""

_USERS = {"alice": "wonderland", "bob": "builder"}


def login(username: str, password: str) -> bool:
    """Return True when the username/password pair is valid."""
    return _USERS.get(username) == password


def logout(session: dict) -> None:
    """Clear a session dict in place."""
    session.clear()
