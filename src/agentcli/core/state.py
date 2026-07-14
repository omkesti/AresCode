"""Session state: the flat message history plus JSON save/resume.

Roles: system / user / assistant / tool. Autosave to ``.agentcli/sessions/<timestamp>.json``;
``--resume`` loads the latest (context.md §4.1, TASKS 1.5).
"""
