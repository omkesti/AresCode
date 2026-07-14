"""ModelProvider protocol: the async streaming chat interface every backend implements.

``chat(messages, **opts) -> AsyncIterator[Chunk]`` plus a non-streaming convenience wrapper
(context.md §4.7, TASKS 1.1).
"""
