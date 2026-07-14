"""Context assembly and token budget: build the prompt, estimate tokens, compact old turns.

Repo map + AGENT.md + compacted history injection; ``len // 4`` token estimate; summarize
the oldest turns at 75% of budget, never compacting the system prompt, the current task, or
the last four tool results (context.md §4.5, TASKS 5.3-5.4).
"""
