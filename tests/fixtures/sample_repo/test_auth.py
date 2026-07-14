"""Tests for the fixture repo's auth module (run by the agent, not by agentcli's CI)."""

from auth import login


def test_login_success():
    assert login("alice", "wonderland") is True


def test_login_wrong_password():
    assert login("alice", "nope") is False


def test_login_unknown_user():
    assert login("carol", "whatever") is False
