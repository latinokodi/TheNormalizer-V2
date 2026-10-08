# Skills applied

The practices this build was held to, and what each one actually changed. A skill that left no mark on the
tree is not listed, because a list of names is not evidence of anything.

Twelve were loaded by name for this build; ten left a change worth pointing at, and the two that did not are
named at the end rather than quietly dropped.

---

## Python

| Skill | What it changed |
|---|---|
| **python-pro** | Full type hints on every public signature and every dataclass field, `X \| None` rather than `Optional[X]`, `from __future__ import annotations` in all five engine modules. `NormalizeSpec` and `NormalizePlan` are frozen dataclasses, so a plan cannot be edited by the thing reading it and `with_measurements` returns a *new* plan rather than mutating the one the window was shown. The exception taxonomy is typed with a stable `reason` tag per subclass (`input`, `shape`, `output`) so the window branches on a tag instead of on prose that is expected to be rewritten. |
| **python-patterns** | The transport decision was made from the workload: a loopback API with a streaming response needs an async server and nothing else, so `aiohttp` and not FastAPI — there is no routing tree, no auth and no schema to validate. One batch at a time is a lock and a slot, not a queue and a worker pool. The engine itself is synchronous and runs on a `threading.Thread`; every blocking call made from a coroutine goes through `asyncio.to_thread`. |
| **python-testing-patterns** | No media files in the suite. Every test is a literal `MediaInfo(...)` with a `Fraction` rate, an argument list built from a plan, or a monkeypatched seam — so the suite runs in 1.5 seconds on a machine with no footage. The seams are the project's own (`process.run`, `normalizer.measure_levels`, `media.probe`) rather than `subprocess.Popen` at large, because patching the standard library tests the mock. `pytest-asyncio` is deliberately absent: the four async needs are real HTTP requests against a real socket, and `asyncio.run` in a helper supplies a loop with no new dependency. |
| **error-handling-patterns** | Catch only what can be acted on, and only at the boundaries: `NormalizeError` becomes a 400/422/409 with its tag, `Cancelled` becomes a job reported as cancelled rather than failed, `FFmpegError` becomes a failure carrying its argument list and the tail of stderr, and anything else is a 500 with the traceback in the log rather than in the response. A refusal names the file, the number and the field — *"the target is 6.0 dBFS, which is outside the range this product works in (-24 to 0 dBFS)"* — because "invalid input" is not a thing a person can fix. The three-state verdict (`passed` / `failed` / `not_checked`) exists so a check that did not run can never render as a pass. |
| **api-design-principles** | Resources are nouns: `POST /api/normalizations` starts a batch and returns **201** with a `Location`, `GET /api/normalizations/current` is the singleton (**204** when idle), and `DELETE` on it is the cancel and is idempotent. A second batch is **409** with `reason: "busy"`, not a queue. A malformed body is a **400** on both routes rather than a **500**, which is checked by a parameterised test over seven malformed bodies. |
| **clean-code** | One class, one responsibility: `Hub` fans out, `Job` holds a file's run, `Batch` holds the queue, `plan_normalize` is pure and writes nothing. The command builders (`sound_graph`, `master_command`, `normalize_commands`) are pure functions from a plan to argument lists, which is what makes *"the picture is copied"* testable without starting a process. No shell anywhere — argument lists only, never a string handed to `cmd` — because a path with a space in it is ordinary on Windows. |
| **lint-and-validate** | `python -m py_compile` on every engine module and `tsc --noEmit` plus a Vite build on the interface were run after every change, and the two suites after every change to the engine. No linter is installed and none is configured — there is no `pyproject.toml` — so `ruff` and `mypy` **were not run** and `docs/TRUTH.md` does not claim they passed. |

## The interface

| Skill | What it changed |
|---|---|
| **react-best-practices** | The window's state is one `useReducer` over one exported `Window` value, not a shelf of `useState`s, because a run is a state machine and four peer booleans can disagree: `running` true while `done` is also true is not a state, it is a bug that renders. The reducer is a pure exported function, so all of the event-stream bookkeeping is tested without a browser — including the case that matters, a job event arriving for a row the operator removed. The event subscription is one `useEffect(..., [])` and the job-id lookup lives in refs, so a state change cannot re-subscribe and replay the engine's history into the log. |
| **typescript-expert** | The tree's strict settings are respected rather than relaxed: `exactOptionalPropertyTypes`, `noUncheckedIndexedAccess`, `noPropertyAccessFromIndexSignature`, `verbatimModuleSyntax`. `exactOptionalPropertyTypes` is what caught the batch request building an `output: undefined` where the field means "you choose" — a field that is *absent* and a field that is *undefined* are different requests, and the type system said so before the engine did. Exhaustive switches end with `const impossible: never = action` so a new action is a compile error rather than a silent drop. |

## The shell, the process and the desktop

| Skill | What it changed |
|---|---|
| **powershell-windows** | Two real defects in the inherited launcher path were kept fixed rather than re-introduced: `$code = $LASTEXITCODE` with no initialisation (it is only set by a *native* command, so it can be `$null`, and `$null -ne 0` is true — a window that closed cleanly was reported as "closed with an error"), and `ConvertTo-Json` without `-Depth`. The launcher uses flat labels and `goto` rather than nested parenthesised blocks, because `cmd.exe` loses the exit code of an `exit /b` inside one. |
| **webapp-testing** | This skill's own advice — *write native Playwright scripts* — was **followed in shape and not in tooling**, and the reason is a finding about this product rather than a preference: `--dump-dom` cannot be used on a page that holds an open event stream, which was measured twice (a health lamp reading "asking" on a machine whose engine was answering, and a browser that never returned at all). So `scripts/check_window.py` drives Chromium over the **DevTools protocol** — a real browser, real evaluation, real screenshots — which is the same thing a Playwright driver does underneath, with no second browser download for a page this application already ships inside one. |

---

## The two that left no mark, named

**`systematic-debugging`** was loaded and its rule *was* applied — every one of the five defects in
`docs/DESIGN.md` §2 was found by measuring rather than by reasoning, and the *"the field is short by exactly
the reorder depth"* shape of §2.3 is that skill's worked example. It is not listed above because it changed
no *file*: what it changed is which of four designs survived, and that is recorded in `DESIGN.md` where the
designs are.

**`api-design-principles`' resources** might have produced a `/api/plans` per source, and the single
`normalization-plans` route that takes a `sources` list is the opposite choice. The skill was applied to the
*shape* of the answer (a noun, a stable error tag, a 201 with a Location) and overridden on the *cardinality*
— because the engine's unit of work is one file and the window's is a batch, and a route per file would put
the batch in the client.
