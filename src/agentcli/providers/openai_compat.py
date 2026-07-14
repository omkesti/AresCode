"""OpenAI-compatible streaming provider over httpx (Ollama / LM Studio / vLLM / cloud).

SSE streaming against ``/v1/chat/completions``; passes num_ctx / temperature / keep_alive as
options; explicit connection and timeout error handling (context.md §4.7, TASKS 1.2).
"""
