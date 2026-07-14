# sample_repo

A tiny self-contained Python project used as a fixture for exercising the agent
(grep -> read -> run tests -> edit). It is intentionally excluded from arescode's own
test collection (`--ignore=tests/fixtures`); its `test_auth.py` is meant to be run *by the
agent*, not by our CI.
