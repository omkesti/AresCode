"""Permission gate: allow / ask / deny verdicts with sandboxing.

Auto-allow read-only tools; ask (with diff or command preview) for writes and shell; hard-deny
path escapes outside the project root and blocklisted commands. Reads only parsed action
fields, never model prose, for prompt-injection containment (context.md §4.6, TASKS 4.1-4.4).
"""
