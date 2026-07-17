# Project Context — AresCode

> A Claude Code–style terminal coding agent that runs entirely on local models via Ollama.
> This document is the single source of truth for the project's goals, architecture, design decisions, and structure. Feed it to any AI assistant or new collaborator to get them fully up to speed.

**Status:** Phases 0–5 complete (M1 Talk, M2 Act, M3 Edit, M4 Trust, M5 Endure); Phase 6 (Polish) next
**Name:** `AresCode`
**Default model:** `qwen2.5-coder:7b` via Ollama (low-VRAM-safe out of the box); `qwen2.5-coder:14b-instruct` is the stronger opt-in — switch at runtime with `/model` and it is remembered as the default from the next launch on (D13)
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
7. Mid-session model switching (`/model`) with safe VRAM hot-swapping — pick from installed models, the previous one is explicitly unloaded before the new one loads, with per-model settings (D12); the last switch is remembered as the next launch's default (D13)

**Explicitly deferred (post-MVP):** sub-agents, MCP support, TODO planner, tree-sitter symbol maps, *automatic* multi-model routing (per-task model selection — distinct from the manual `/model` switch above), IDE integration, hooks, custom slash commands.

---

## 2. The core design constraint

**Everything in this architecture follows from one fact: a local coding model is drastically weaker than the frontier models these harnesses were designed around.**

Claude Code gets away with a minimal harness because Claude is smart. A local model is not. Therefore this project is **harness-heavy**:

- Strict, training-familiar output protocols instead of free-form JSON
- Lenient, forgiving parsers that absorb the model's formatting mistakes
- Retry-with-error-feedback loops on every failed edit
- A deliberately tiny tool surface (6 tools) so instruction-following doesn't dilute
- Aggressive context discipline (small models degrade fast as context grows)

**Rule of thumb for every design decision:** "Would this work if the model gets it 80% right?" If the answer requires 99% model accuracy, redesign the harness, not the prompt.

**On the model choice (D11, D13).** D11 introduced `qwen2.5-coder:14b-instruct` as a noticeably stronger instruction-follower; D13 makes the light `qwen2.5-coder:7b` the **out-of-the-box default** again so a first run is low-VRAM-safe, with the 14B one `/model` switch away and *remembered* as the default from the next launch on. Either way **the harness above is retained unchanged.** None of it was a 7B-specific workaround — the lenient parser, retry loops, whole-file fallback, and 6-tool surface are *model-robustness* measures that keep the agent reliable across any local model (and across a bad generation from a good one). A stronger model raises the floor; it does not remove the reason the floor exists. The 80%-right rule of thumb still governs every decision.

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

**Testing priority:** `parser.py` and `edit.py` get the deepest test coverage in the project — table-driven tests over a corpus of real malformed model outputs collected during development. This is where local-model unreliability concentrates.

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

1. **Repo map** — gitignore-filtered file tree with sizes, injected into the system prompt (capped ~1,500 tokens). Built at session start and **rescanned within the session** whenever a tool changes the working tree (a successful write/edit, or any bash run), so the model never works from a stale snapshot; `/map` also rescans on demand. Post-MVP: tree-sitter top-level symbols per file, Aider-style. **No embeddings/RAG in v1** — the agent navigates with grep/read like Claude Code does.
2. **Project memory (`ARES.md`)** — user-editable Markdown in the project root: conventions, commands, architecture notes. Loaded into every system prompt. Direct clone of the CLAUDE.md pattern. The agent may propose additions but only writes to it with permission.
3. **Compaction** — token count estimated at `len(text) // 4`. At 75% of the context budget, summarize the oldest turns into a single assistant message ("Summary of earlier work: …") and drop the originals. **Never compact:** the system prompt, the current user task statement, and the last 4 tool results.

**Context budget config:** default `num_ctx = 16384`. (See 4.7 — Ollama's silent 4k default is a known killer.)

**As built (Phase 5).** All three mechanisms live in **`core/context.py`**, which owns the token-budget primitives (`estimate_tokens` = `len // 4`; `budget_for` = `num_ctx` − a 1,500-token reply reserve, `REPLY_RESERVE_TOKENS`); `core/models.py` re-exports them so the D12 switch path keeps importing from `arescode.core.models` unchanged. **Repo map** (`repo/repomap.py`, TASKS 5.1): a gitignore-filtered tree with file sizes, sharing the search tools' ignore rules, rendered at the deepest nesting depth whose text fits ~1,500 tokens (breadth-first depth truncation) with a per-directory width cap so one huge folder can't dominate; `/map` redisplays it. **ARES.md** (TASKS 5.2): `load_project_memory` reads it from the project root; `assemble_system_prompt` composes base prompt + `ARES.md` + repo map (empty sections omitted). The in-session `/init` command authors `ARES.md` model-side — it runs an ordinary agent turn on `INIT_INSTRUCTION` (on a throwaway state, so the big instruction never enters the real history) so the model explores the repo and writes the file itself, rather than dropping a static scaffold. **Compaction** (TASKS 5.4): `maybe_compact` fires at the 75% threshold from the top of each loop step, folding the compactable middle of the history into one `assistant` "Summary of earlier work" message produced by a dedicated `provider.complete` call — the **current task message is pinned by identity** and the last 4 messages are protected, so both survive; a failed/empty summary degrades to `hard_truncate`. `/compact` forces it (`compact_now`); a subtle indicator fires when it runs. The folded history is ordinary messages, so `SessionState.save`/`--resume` round-trip it. The `[HAND]` loop gained only a `num_ctx` parameter and the one `maybe_compact` call (the algorithm is entirely in `context.py`), added under the author's explicit authorization.

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
- `num_ctx` **must be set explicitly** — Ollama defaults to 4096 tokens regardless of model capability, which silently truncates agent context and looks like "the model is dumb." Set 16k default; 32k is qwen2.5-coder's max but watch KV-cache VRAM.
- **14B VRAM budget:** the opt-in `qwen2.5-coder:14b-instruct` Q4 is ≈9GB of weights, and the KV cache grows with `num_ctx` on top of that. On a GPU with <12GB VRAM (e.g. an RTX 3050 6GB) Ollama offloads layers to the CPU and generation slows sharply; the weights + a large KV cache spill to system RAM and crawl. Mitigation: drop `num_ctx` to 8192, or stay on the default `qwen2.5-coder:7b` (≈4.7GB) on low-VRAM machines.
- `temperature`: 0.0–0.2 for agentic work.
- `keep_alive`: set generously (e.g. `30m`) so the model isn't reloaded between turns.

**Multi-model switching and per-model `num_ctx` (D12).** The user can switch the active model
mid-session with `/model` (no arg → interactive picker over installed models; `/model <name>` →
direct switch with prefix-matching). Because a sub-12GB GPU can only hold one model at a time, the
switch enforces **exclusive residency**: the current model is unloaded from VRAM *before* the target
is warmed. This uses Ollama's **native `/api/*` endpoints**, which the OpenAI-compat surface does not
expose — so a small dedicated admin client (`providers/ollama_admin.py`) owns exactly four calls
(`/api/tags`, `/api/ps`, and `/api/generate` for unload/warmup) and is **strictly isolated from the
chat provider** (the chat path stays on `/v1/chat/completions`, preserving D5). If the backend is not
Ollama, the admin calls 404 and the feature degrades gracefully to a name-only switch. The switch
lifecycle (validate → block-if-mid-turn → unload → warmup → update state → recompute budget →
compact if the window shrank) lives in `core/models.py` (`ModelManager`).

Each model carries its own `num_ctx`/`temperature` via `[models."<tag>"]` config sections; a model
with no section inherits the top-level defaults:

| Model | `num_ctx` | Rationale |
|---|---|---|
| `qwen2.5-coder:7b` (≈4.7GB Q4) | 16384 | Weights + KV cache fit VRAM comfortably; use the fuller window. |
| `qwen2.5-coder:14b-instruct` (≈9GB Q4) | 8192 | On a <12GB GPU the weights already spill to CPU; a smaller KV cache keeps generation from crawling. |
| any other tag | top-level default | Falls back to the `num_ctx`/`temperature` defaults in the config root. |

On a switch the token budget is recomputed for the new window; if the history no longer fits a
*smaller* window it is compacted immediately (Phase 5 summarization once built — TASKS 5.4; until
then a visible hard-truncation of the oldest turns). Edit telemetry (§4.3 / TASKS 3.6) is tagged
with the model that produced each edit, so `/stats` groups by model.

**Remembered model (D13).** The out-of-the-box default is the light `qwen2.5-coder:7b`, so a first
run works on a low-VRAM box without tuning. A successful `/model` switch writes the chosen tag to
`~/.arescode/last_model` (bare text, machine-managed — kept out of the hand-edited `config.toml` so
remembering a choice never rewrites a user's comments). On the next launch `load_config` seeds that
model *just above the built-in default*, so a capable machine that switches to the 14B keeps it —
while an explicit `model` in a config file or a `--model` flag still overrides it. A launch-time
`--model` is a per-launch override and is **not** remembered; only the in-session `/model` switch is.

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
3. **M3 — Edit:** SEARCH/REPLACE applier + retry-with-feedback + whole-file fallback. The make-or-break milestone. *(Proves: edits land reliably on a local model.)*
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
| D11 | Upgrade default model to `qwen2.5-coder:14b-instruct`; harness unchanged; re-baseline edit telemetry | Stay on 7B; or drop harness weight now that the model is stronger | Stronger instruction-following at a modest VRAM cost, with zero harness changes (robustness ≠ 7B workaround); 7B edit-success baselines are now stale and must be re-measured |
| D12 | Mid-session multi-model switching with a **native admin API** (`ollama_admin.py`) kept isolated from the chat provider; exclusive VRAM residency (unload-before-load); per-model `num_ctx`/`temperature` | Automatic per-task routing; keeping only one model per process (restart to switch); driving unload through the chat provider | A 6GB GPU can hold one model at a time, so a hot-swap must evict the old one first — an Ollama-native operation the OpenAI-compat surface can't do. Isolating it preserves D5 portability (chat stays on `/v1`); admin 404s degrade to a name-only switch. Manual `/model` only — *automatic routing* stays deferred |
| D13 | Default to the light `qwen2.5-coder:7b`, and **remember the last `/model` switch** (`~/.arescode/last_model`) as the next launch's default | Keep the 14B as the hard default (D11); or let the remembered choice override config/CLI too; or rewrite `model` back into `config.toml` on switch | A first run should work on a low-VRAM box out of the box; a capable machine switches to the 14B once and it sticks. The remembered model overrides *only* the built-in default — an explicit `model` in config or a `--model` flag still wins, so pins stay authoritative. A separate machine-managed file avoids rewriting the hand-edited `config.toml`. Harness unchanged (robustness ≠ a 7B workaround) |

---

## 9. Known risks

- **Edit reliability floor:** even with the full cascade, a local model may fail multi-file or long-range edits. Mitigation: whole-file fallback, small-diff prompting, and honest measurement (track edit success rate from day one).
- **Stale edit-success baseline (post-D11):** every edit-success number to date — including Phase 3's ≥8/10 gauntlet result — was measured on `qwen2.5-coder:7b` and is now stale. The telemetry counters are *not* reset in code, but treat the recorded figures as unverified until Phase 3's 10-task edit gauntlet is re-run **once** on `qwen2.5-coder:14b-instruct` to establish the new baseline. `/stats` reports raw counters; it does not know which model produced them.
- **VRAM ceiling (<12GB GPUs, e.g. RTX 3050 6GB):** the default is the ≈4.7GB `qwen2.5-coder:7b`, which fits comfortably; the ceiling bites only when the user opts into `qwen2.5-coder:14b-instruct` (D13). Its Q4 weights are ≈9GB, so on a sub-12GB GPU Ollama offloads to CPU and the weights + a large KV cache spill to system RAM and crawl. Mitigation: keep the 7B default, a smaller per-model window for the 14B (8192, drop from the 16k default), aggressive compaction, and documented `num_ctx`/VRAM tradeoffs.
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
