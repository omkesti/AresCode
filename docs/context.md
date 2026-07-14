# Project Context — AresCode

> A Claude Code–style terminal coding agent that runs entirely on local models via Ollama.
> This document is the single source of truth for the project's goals, architecture, design decisions, and structure. Feed it to any AI assistant or new collaborator to get them fully up to speed.

**Status:** Phases 0–4 complete (M1 Talk, M2 Act, M3 Edit, M4 Trust); Phase 5 (Endure) next
**Name:** `AresCode`
**Primary model target:** `qwen2.5-coder:7b` via Ollama
**Author:** Om — solo project

---

## 1. What this project is

A CLI tool that works like Claude Code but is powered by locally installed models. The user launches it inside a project directory; the agent can then:

- Read, create, and edit files in the project
- Run shell commands (tests, builds, git, etc.)
- Search the codebase (grep/glob)
- Iterate autonomously in a loop — read → think → act → observe — until the task is done or the user stops it

**MVP scope (fundamental features only):**

1. Interactive terminal REPL with streaming responses
2. Six core tools: `read_file`, `write_file`, `edit_file`, `bash`, `grep`, `glob`
3. Single-threaded agent loop with a hard step cap
4. Permission gate (auto-allow reads, confirm writes/shell with diff previews)
5. Context management: repo map, `ARES.md` project memory, token-budget compaction
6. Session persistence (save/resume conversation state)

**Explicitly deferred (post-MVP):** sub-agents, MCP support, TODO planner, tree-sitter symbol maps, multi-model routing, IDE integration, hooks, custom slash commands.

---

## 2. The core design constraint

**Everything in this architecture follows from one fact: qwen2.5-coder:7b is drastically weaker than the frontier models these harnesses were designed around.**

Claude Code gets away with a minimal harness because Claude is smart. A 7B model is not. Therefore this project is **harness-heavy**:

- Strict, training-familiar output protocols instead of free-form JSON
- Lenient, forgiving parsers that absorb the model's formatting mistakes
- Retry-with-error-feedback loops on every failed edit
- A deliberately tiny tool surface (6 tools) so instruction-following doesn't dilute
- Aggressive context discipline (small models degrade fast as context grows)

**Rule of thumb for every design decision:** "Would this work if the model gets it 80% right?" If the answer requires 99% model accuracy, redesign the harness, not the prompt.

---

## 3. High-level architecture

Layered, single-threaded, one flat message history. Modeled on Claude Code's master-loop design (its loop, codenamed `nO`, is a plain while-loop that runs as long as the model's response contains tool calls; plain text ends the turn). No multi-agent orchestration, no graph frameworks.

```
┌─────────────────────────────────────────────┐
│  TUI / REPL layer                           │
│  prompt_toolkit input · rich rendering      │
│  slash commands · interrupt handling        │
└──────────────────┬──────────────────────────┘
                   │ user prompt
┌──────────────────▼──────────────────────────┐
│  Agent core — single-threaded master loop   │
│  context assembly → model call → parse →    │
│  gate → execute → append results → repeat   │
│  (flat message history, hard step cap)      │
└───────┬──────────────────────────┬──────────┘
        │ prompt/stream            │ tool calls
┌───────▼─────────┐      ┌─────────▼──────────┐
│ Model provider   │      │ Permission gate    │
│ OpenAI-compat    │      │ allow / ask / deny │
│ streaming client │      └─────────┬──────────┘
└───────┬─────────┘                 │ approved
┌───────▼─────────┐      ┌─────────▼──────────┐
│ Ollama server    │      │ Tool executor      │
│ qwen2.5-coder:7b │      │ read·edit·bash·grep│
└──────────────────┘      └─────────┬──────────┘
                          ┌─────────▼──────────┐
                          │ Workspace          │
                          │ project files, git │
                          └────────────────────┘
```

**Turn flow:** user prompt → context assembly (system prompt + repo map + ARES.md + compacted history) → model call → lenient parse of actions → each action passes the permission gate → executor runs it → truncated result appended to history → loop. When the model emits plain text with no actions, the turn ends and control returns to the REPL.

---

## 4. Low-level design

### 4.1 The master loop (`core/loop.py`)

```python
async def run_turn(user_msg: str, state: SessionState) -> str:
    state.history.append(user(user_msg))
    for step in range(MAX_STEPS):              # hard cap, default 25
        if state.interrupted:                  # Esc/Ctrl-C sets this flag
            return "Interrupted by user"
        prompt = assemble_context(state)       # system + repo map + compacted history
        resp = await provider.chat(prompt)     # streamed to UI as it arrives
        actions = parse_actions(resp)          # lenient parser (4.3)
        if not actions:
            return resp.text                   # plain text → turn over
        for act in actions:
            verdict = gate.check(act)          # allow / ask user / deny
            result = executor.run(act) if verdict.allowed else verdict.denial_msg
            state.history.append(tool_result(act, truncate(result)))
    return "Step limit reached — ask user how to proceed"
```

Design rules:
- **One thread, one flat history.** No branches, no competing agent personas.
- **Interrupt flag** checked between steps — the cheap version of Claude Code's steering queue. Post-MVP: a proper async input queue so the user can inject instructions mid-task.
- **Hard step cap** prevents infinite loops (a real risk with 7B models that "forget" they finished).

### 4.2 Output protocol — the most important decision in the project

**Decision: text-based action protocol, NOT native JSON tool calling.**

Rationale (backed by Aider's public benchmarks): function-calling APIs perform *worse* than plain-text formats for code editing, and weak models frequently mangle JSON — especially escaping multiline code inside JSON string arguments. Qwen2.5-coder was trained heavily on git-merge-conflict-style markers, so we use them.

The model is instructed (in the system prompt) to emit actions like:

```
<tool>read_file</tool><path>src/auth.py</path>

<tool>bash</tool><cmd>pytest tests/ -x -q</cmd>

src/auth.py
<<<<<<< SEARCH
def login(user):
=======
def login(user, remember=False):
>>>>>>> REPLACE
```

- Simple tools (read, bash, grep, glob) use flat XML-ish tags — trivially parseable, no escaping problems.
- File edits use **Aider-style SEARCH/REPLACE blocks** — the format the model has seen most in training.
- New files use `write_file` with a fenced code block, or a SEARCH/REPLACE with an empty SEARCH section.
- **Fallback — "whole file" mode:** for files under ~150 lines, or after 2 failed SEARCH/REPLACE attempts on the same file, instruct the model to return the complete updated file in a fenced block. Proven to be the most reliable format for weaker models.

### 4.3 The lenient parser (`core/parser.py`)

The parser is written assuming the model *almost* gets the format right. It must absorb:

- Marker-length drift (`<<<<<<` vs `<<<<<<<`), stray whitespace, missing language tags on fences
- Filename placed 1–3 lines above/below where expected (hunt for it)
- Nested fences inside code content
- Multiple actions per response, in any order

**SEARCH-block matching cascade (in `tools/edit.py`):**
1. Exact string match
2. Whitespace-normalized match (strip trailing spaces, normalize indentation)
3. Fuzzy match — `difflib.SequenceMatcher` ratio > 0.9 on a sliding window
4. **Fail loudly back to the model**: append a tool result like
   `"SEARCH block not found in src/auth.py. Closest match (line 42, 87% similar): <snippet>. Re-read the file and retry with the exact current content."`
   Retry cap: 2–3 per edit, then fall back to whole-file mode.

**Testing priority:** `parser.py` and `edit.py` get the deepest test coverage in the project — table-driven tests over a corpus of real malformed model outputs collected during development. This is where 7B unreliability concentrates.

### 4.4 Tool set (MVP — exactly six)

| Tool | Contract | Safety limits |
|---|---|---|
| `read_file` | path, optional line offset/limit | cap ~2,000 lines / 50KB per read; returns numbered lines |
| `write_file` | path + full content; **new files only** | refuses to overwrite existing files (use edit_file) |
| `edit_file` | SEARCH/REPLACE blocks | matching cascade + retry; shows diff before apply |
| `bash` | single command string | cwd locked to project root; 60s default timeout; output truncated to ~200 lines (head + tail); no interactive commands |
| `grep` | pattern + optional path/glob filter | wraps ripgrep (`rg`); result cap ~100 matches |
| `glob` / `list_dir` | pattern | respects `.gitignore` via pathspec; depth-capped |

No more tools until the MVP works end-to-end. Every added tool measurably dilutes a small model's instruction-following.

### 4.5 Context management (`core/context.py`)

Small models die silently when context bloats. Three mechanisms:

1. **Repo map** — built at session start: gitignore-filtered file tree with sizes, injected into the system prompt (capped ~1,500 tokens). Post-MVP: tree-sitter top-level symbols per file, Aider-style. **No embeddings/RAG in v1** — the agent navigates with grep/read like Claude Code does.
2. **Project memory (`ARES.md`)** — user-editable Markdown in the project root: conventions, commands, architecture notes. Loaded into every system prompt. Direct clone of the CLAUDE.md pattern. The agent may propose additions but only writes to it with permission.
3. **Compaction** — token count estimated at `len(text) // 4`. At 75% of the context budget, summarize the oldest turns into a single assistant message ("Summary of earlier work: …") and drop the originals. **Never compact:** the system prompt, the current user task statement, and the last 4 tool results.

**Context budget config:** default `num_ctx = 16384`. (See 4.7 — Ollama's silent 4k default is a known killer.)

### 4.6 Permission gate (`permissions/gate.py`)

Deny-first philosophy, per-action approval:

- **Auto-allow:** `read_file`, `grep`, `glob` (read-only)
- **Ask with preview:** `edit_file` / `write_file` → render a colored unified diff, prompt `[y]es / [n]o / [a]lways for this file`
- **Ask for shell:** `bash` prompts unless the command's first token matches the session allowlist (user can answer "always allow `pytest`")
- **Hard deny (never prompted, never overridable by the model):**
  - Any path resolving outside the project root (check `os.path.realpath` against root — catches `../` and symlink escapes)
  - Regex blocklist: `rm -rf /`, `git push --force` to main, `curl ... | sh`, `sudo`, writes to `~/.ssh`, `.env` exfiltration patterns
- Session allowlist resets on exit; a persistent allowlist lives in project config.

**Security note (relevant to Sentinia thinking):** tool results are untrusted input. A file or command output containing "ignore previous instructions" must never change gate behavior — the gate reads only the parsed action, never model prose. Prompt-injection hardening of the system prompt is a post-MVP work item.

**As built (Phase 4).** `Gate.check(action) -> Verdict{ALLOW | ASK | DENY}` is a pure function of parsed action fields. The interactive gate runs **inline in the loop** (`core/loop.py:_permit`), matching the §4.1 flow above: allow / ask-and-approve / deny resolves *before* the tool-execution timer, so a denial or the user's decision time never inflates tool latency, and a DENY becomes a failed `ToolResult` reported to the model as a tool error. The `Executor` shares the same `Gate` as a **belt-and-suspenders hard-deny backstop** (`Executor._check_permission` — a blocklisted command or path escape can never execute regardless of caller; ASK/ALLOW pass through, since approval already happened in the loop). ASK verdicts are answered by an injected `Approver` (`ui/approve.py`): `interactive_approver` reads a single keystroke, `auto_approver` backs `--yolo`. Path containment uses `Path.resolve()` (realpath) against the resolved root; the blocklist is a regex table (`DEFAULT_BLOCKLIST`). The session allowlist lives on the `Gate` (`/allow`, `/deny`, or an `a` answer); the persistent allowlist is the `allow_commands` / `allow_paths` config keys. The approval preview is a **true unified diff** from a read-only dry-run (`edit.py:preview_edit` / `preview_write`), leaving the hand-written `apply_edit` cascade untouched. `core/loop.py` and `tools/edit.py` are `[HAND]` files (D10); the Phase 4 additions were made by Claude Code under the author's explicit authorization.

### 4.7 Model provider (`providers/`)

- **Protocol:** one `ModelProvider` interface — `async def chat(messages, **opts) -> AsyncIterator[Chunk]` (streaming).
- **Implementation:** `openai_compat.py` using `httpx` against Ollama's OpenAI-compatible endpoint (`http://localhost:11434/v1/chat/completions`). Choosing the OpenAI-compat surface (not Ollama's native API) means LM Studio, vLLM, llama.cpp server, Groq, OpenRouter, and real Claude/GPT all work with a config change.
- **No LangChain / LiteLLM.** The abstraction is ~50 lines; owning it is the point.

**Ollama-specific gotchas (hard-won, do not skip):**
- `num_ctx` **must be set explicitly** — Ollama defaults to 4096 tokens regardless of model capability, which silently truncates agent context and looks like "the model is dumb." Set 16k default; 32k is qwen2.5-coder's max but watch KV-cache VRAM on the RTX 3050 6GB (7B Q4 + 32k KV cache will spill to RAM and crawl).
- `temperature`: 0.0–0.2 for agentic work.
- `keep_alive`: set generously (e.g. `30m`) so the model isn't reloaded between turns.

### 4.8 System prompt (`prompts/system.md`)

Versioned in the repo, never hardcoded in Python. Contents: role definition, the action protocol spec with 2–3 few-shot examples per tool, the SEARCH/REPLACE rules, "read before you edit" and "run tests after edits" behavioral rules, the repo map, and ARES.md contents. Keep the static portion under ~2,000 tokens — every token here is paid every single model call.

---

## 5. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | Fastest iteration for Om; native fit with ML tooling and future eval harness. (Claude Code itself is TypeScript + Ink — valid alternative, not chosen.) |
| HTTP / streaming | `httpx` (async) | Direct control of streaming; no framework tax |
| REPL / input | `prompt_toolkit` | Multiline input, history, keybindings, interrupt handling |
| Terminal rendering | `rich` | Markdown, syntax highlighting, colored diffs, spinners |
| CLI entry | `typer` | Clean arg parsing, subcommands |
| Config & schemas | `pydantic` + TOML (`~/.arescode/config.toml` + per-project `.arescode.toml`) | Validated config, layered overrides |
| Gitignore handling | `pathspec` | Filter repo map and glob results |
| Git operations | `subprocess` git (raw) | Fewer deps; GitPython only if it earns its place |
| Fuzzy matching | stdlib `difflib` | Edit-block cascade step 3 |
| Search | ripgrep (`rg`) binary | Fast, battle-tested; fall back to Python scan if absent |
| Code parsing (post-MVP) | `tree-sitter` + `tree-sitter-languages` | Symbol-level repo map |
| Deliberately excluded | LangChain, LangGraph, LiteLLM, embeddings/vector DBs | The loop IS the project; frameworks hide exactly the parts worth learning |

---

## 6. Project structure

```
AresCode/
├── pyproject.toml
├── README.md
├── context.md                 # this file
├── prompts/
│   └── system.md              # versioned system prompt
├── src/arescode/
│   ├── main.py                # typer entry point, session bootstrap
│   ├── config.py              # pydantic settings, TOML loading (global + project)
│   ├── core/
│   │   ├── loop.py            # the master while-loop (4.1)
│   │   ├── context.py         # context assembly, token budget, compaction (4.5)
│   │   ├── parser.py          # lenient action + edit-block parser (4.3)
│   │   └── state.py           # flat message history, session save/resume (JSON)
│   ├── providers/
│   │   ├── base.py            # ModelProvider protocol (streaming)
│   │   └── openai_compat.py   # Ollama / LM Studio / vLLM / cloud
│   ├── tools/
│   │   ├── registry.py        # tool schemas, dispatch, result truncation
│   │   ├── files.py           # read_file, write_file
│   │   ├── edit.py            # SEARCH/REPLACE cascade applier (4.3)
│   │   ├── shell.py           # sandboxed bash (timeout, cwd lock, truncation)
│   │   └── search.py          # grep (ripgrep wrapper), glob
│   ├── permissions/
│   │   └── gate.py            # allow/ask/deny, diff preview, allowlists (4.6)
│   ├── repo/
│   │   └── repomap.py         # file tree now; tree-sitter symbols later
│   └── ui/
│       ├── repl.py            # prompt_toolkit loop, slash commands, interrupts
│       └── render.py          # rich output: streaming md, diffs, tool traces
└── tests/
    ├── test_parser.py         # HIGHEST priority — corpus of malformed outputs
    ├── test_edit.py           # matching cascade, retry behavior
    ├── test_gate.py           # path escapes, blocklist
    └── fixtures/              # sample repos, recorded model outputs
```

---

## 7. Build order (milestones)

1. **M1 — Talk:** provider + streaming REPL. Chat with qwen2.5-coder in the terminal with rendered markdown. *(Proves: streaming, num_ctx config.)*
2. **M2 — Act:** parser + `read_file`, `bash`, `grep`, `glob` + the loop. Agent can explore a repo and run tests. *(Proves: the loop terminates correctly.)*
3. **M3 — Edit:** SEARCH/REPLACE applier + retry-with-feedback + whole-file fallback. The make-or-break milestone. *(Proves: edits land reliably on a 7B model.)*
4. **M4 — Trust:** permission gate, diff previews, path sandboxing, blocklist.
5. **M5 — Endure:** compaction, ARES.md, repo map, session save/resume.
6. **M6 — Polish:** slash commands (`/clear`, `/compact`, `/model`, `/allow`), config file, packaging (`pipx install`).

Each milestone ships as a usable tool. Do not start M(n+1) before dogfooding M(n) on a real project.

**Definition of done for MVP:** in a real repo, the agent can take "fix the failing test in X", find the file, edit it, rerun the tests, and report success — with every write/command approved through the gate — using only the local model.

---

## 8. Key decisions log

| # | Decision | Alternatives rejected | Reason |
|---|---|---|---|
| D1 | Single-threaded master loop, flat history | LangGraph state machine, multi-agent | Claude Code proves simple loops win on debuggability; frameworks hide the learning |
| D2 | Text protocol + SEARCH/REPLACE for edits | Native JSON tool calling | Weak models mangle JSON escaping; markers are training-familiar (Aider benchmarks) |
| D3 | Lenient parser with fuzzy cascade + model retry | Strict parse, fail hard | The harness must absorb 7B mistakes; reliability lives in the harness, not the model |
| D4 | Whole-file fallback for small files / repeated failures | SEARCH/REPLACE only | Most reliable format for weak models per Aider's benchmarks |
| D5 | OpenAI-compat endpoint | Ollama native API | Free portability to LM Studio/vLLM/cloud with zero code change |
| D6 | 6-tool MVP surface | Rich toolset day one | Small models' instruction-following dilutes per added tool |
| D7 | grep/read navigation, no RAG | Embeddings + vector search | Matches Claude Code; avoids infra; revisit only if navigation demonstrably fails |
| D8 | Deny-first permission gate, per-action approval | YOLO auto-approve mode | Safety default; auto-mode can be a flag later |
| D9 | Python | TypeScript + Ink | Iteration speed for a solo dev; TS remains the "if rewriting" option |
| D10 | Hand-write `loop.py` + `parser.py` first, no AI codegen | Full AI-assisted build | Explicit skill-building goal: these ~300 lines are the soul of the project |

---

## 9. Known risks

- **Edit reliability floor:** even with the full cascade, a 7B model may fail multi-file or long-range edits. Mitigation: whole-file fallback, small-diff prompting, and honest measurement (track edit success rate from day one).
- **VRAM ceiling (RTX 3050 6GB):** 7B Q4 + large KV cache spills to system RAM. Mitigation: default 16k context, aggressive compaction, document `num_ctx`/VRAM tradeoffs.
- **Loop pathologies:** small models re-read the same file forever or declare victory early. Mitigation: step cap, duplicate-action detection (same tool + same args twice in a row → inject a nudge message).
- **Prompt injection via tool results:** file contents and command output are untrusted. Gate logic never consults model prose; hardening is a tracked post-MVP item.
- **Scope creep:** the Claude Code feature list is enormous. The MVP definition in §1 is the contract — anything else goes to a `LATER.md`.

---

## 10. References

- **Claude Code architecture teardowns** — single-threaded master loop ("nO"), flat history, per-action permissions, compaction: ZenML LLMOps database entry; PromptLayer "Behind the scenes of the master agent loop"; arXiv 2604.14228 "Dive into Claude Code".
- **Aider** — edit formats ("whole" vs "diff" SEARCH/REPLACE), function-calling benchmark results, lenient `find_original_update_blocks` parser: aider.chat/docs/more/edit-formats.html, aider.chat/docs/benchmarks.html.
- **ReAct** (Yao et al., 2022) — the reason+act loop the whole design rests on.
- **SWE-agent** (Yang et al., 2024) — agent-computer interface design: tool ergonomics beat prompt cleverness.
- **CodeAct** (Wang et al., 2024) — executable code as the action space.
- **Input Systems, "aider vs pi"** (2026) — where harnesses absorb model mistakes; "the model isn't where edits get reliable; the harness is."
