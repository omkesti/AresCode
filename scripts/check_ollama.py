"""Verify a local Ollama server for AresCode (TASKS 0.6).

Checks, in order:
  1. the OpenAI-compatible endpoint responds and lists the target model;
  2. a tiny chat completion succeeds with a num_ctx override applied;
  3. (best effort) the requested num_ctx does not exceed the model's own maximum, warning
     if it does since Ollama will silently clamp it.

Usage:
    python scripts/check_ollama.py [--base-url URL] [--model TAG] [--ctx N]

Exit code is 0 unless the endpoint is unreachable or the completion fails; missing models
and clamped context sizes are reported as warnings, not hard failures.
"""

from __future__ import annotations

import argparse
import sys

import httpx

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5-coder:14b-instruct"
DEFAULT_CTX = 16384


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local Ollama for AresCode.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ctx", type=int, default=DEFAULT_CTX)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    warnings = 0

    # 1. Endpoint reachable + model listed (OpenAI-compat surface).
    try:
        resp = httpx.get(f"{base}/v1/models", timeout=10)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        print(f"FAIL  OpenAI-compat endpoint unreachable at {base}/v1/models: {exc}")
        print("      Is the server running?  Start it with:  ollama serve")
        return 1

    model_ids = [entry.get("id") for entry in resp.json().get("data", [])]
    if args.model in model_ids:
        print(f"PASS  model '{args.model}' is available")
    else:
        warnings += 1
        print(f"WARN  model '{args.model}' not found. Available: {model_ids or '(none)'}")
        print(f"      Pull it with:  ollama pull {args.model}")

    # 2. Tiny completion with a num_ctx override (Ollama accepts native options in the body).
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 8,
        "temperature": 0,
        "options": {"num_ctx": args.ctx},
    }
    try:
        resp = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=120)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"PASS  chat completion succeeded with num_ctx={args.ctx}: {content!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  chat completion failed: {exc}")
        return 1

    # 3. Best-effort: warn if the requested context exceeds the model's maximum.
    try:
        show = httpx.post(f"{base}/api/show", json={"model": args.model}, timeout=10)
        if show.status_code == 200:
            info = show.json().get("model_info", {})
            max_ctx = next(
                (v for k, v in info.items() if k.endswith("context_length")), None
            )
            if isinstance(max_ctx, int) and args.ctx > max_ctx:
                warnings += 1
                print(
                    f"WARN  requested num_ctx={args.ctx} exceeds the model maximum "
                    f"{max_ctx}; Ollama will clamp it."
                )
            elif isinstance(max_ctx, int):
                print(f"INFO  model context maximum is {max_ctx} tokens")
    except Exception:  # noqa: BLE001 - this probe is optional
        pass

    print("OK - all checks passed" if warnings == 0 else f"COMPLETED with {warnings} warning(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
