# PR Review: 021_run_agent_in_docker_fixes

- Branch: `feat/021_run_agent_in_docker_fixes`
- Reviewed commit: `ae03329`
- Plan: `docs/plans/checking/021_run_agent_in_docker_fixes.md`
- Scope reviewed: `docs/example_run_agent_in_docker.sh`, `workflow/commands/workflow.py`,
  `tests/test_workflow_commands.py`, `tests/test_integration_workflow.py`

## Summary

The change delivers the three planned items: a mandatory `agent|venv|shell|hook`
subcommand grammar in the example docker script, a unified `hook REPO` mechanism
shared by builder/reviewer (`check`) and canon (new `canon_pre_commit`), and a
canon venv created at `init` via a throwaway container. The Python side is a clean
DRY consolidation — container-name/path conventions now live only in the script,
and `_hook_command`/`_create_venv_command` are single builders. Implementation
matches the plan closely.

Verification: `uv run pytest -q` → 365 passed; `uv run mypy workflow tests` →
clean. Tests were updated consistently with the new command strings, including the
previously-broken `test_check_runs_pre_commit_and_prints_review_reminder`.

No blocking correctness defect found in the diff itself. The main risk is a runtime
assumption about the container image that cannot be verified from this repo (see
Finding 1); the plan already flags manual Docker verification as required.

## Findings

### 1. (High — verify manually) Throwaway `hook` container drops `--env PROJECT`/`--env AGENT`; hook tools may not be on PATH

The whole refactor rests on: "a throwaway container that mounts a repo already has
a warm, self-contained venv." That is true for the *files* (venv built with
`UV_LINK_MODE=copy` lives on the host mount), but nothing in the `hook` dispatch
*activates* that venv:

```
exec docker run --rm --user auggie \
    -v "${PROJECT_DIR}/${REPO}:${REPO_PATH}" --workdir "$REPO_PATH" \
    gronsti-pi bash -lc "./.git-pre-commit-hook"
```

`.git-pre-commit-hook` invokes `black`/`ruff`/`python -m pytest`/`mypy` directly (no
`uv run`, no explicit `activate`), so those tools must already be on `PATH`. The
only thing that can put the per-repo `.venv/bin` on `PATH` is the image's
`~/.bashrc` (sourced by `bash -lc`).

The persistent agent containers are created with `--env PROJECT` and `--env AGENT`
set; the throwaway `hook` (and canon `venv`) containers deliberately omit them. If
the `gronsti-pi` `~/.bashrc` activation logic references `$AGENT`/`$PROJECT` (very
plausible, since those are exported precisely for the persistent containers) rather
than activating `./.venv` relative to the working directory, then the hook will run
with the wrong/absent venv and fail with `black: command not found` (or import
errors under pytest/mypy). This would be a behavior change vs. the old
`docker exec` into the persistent container, which *did* have those env vars.

Recommendation: confirm during manual verification that `~/.bashrc` activates the
venv purely from the working directory. If it depends on `$AGENT`/`$PROJECT`, either
pass those `--env` values to the `hook`/canon-`venv` runs, or make the hook
self-activating (e.g. run under `uv run` / `source .venv/bin/activate` in the exec'd
command). The canon `venv` path is not affected because its command is cwd-relative
(`uv venv --seed && source .venv/bin/activate`).

### 2. (Low) `init` now writes a `.venv` into the human's `canon` working tree

Creating a canon venv is intended and necessary for `canon_pre_commit`, but it
deposits `.venv/` (and uv caches) into the operator's real working repo. This repo
gitignores `.venv`, but the example script targets arbitrary projects. If a target
project's `.gitignore` does not exclude `.venv`, it will show up in canon's
`git status` and risk being committed. Worth a one-line note in the plan/script that
projects must gitignore `.venv` in canon.

### 3. (Low — pre-existing, surfaced by this block) `agent` dispatch likely drops `--model`

```
EXEC_CMD=(docker exec -it ... "$CONTAINER" bash -c "source ~/.bashrc && pi")
[[ -n "$MODEL" ]] && EXEC_CMD+=(--model "$MODEL")
exec "${EXEC_CMD[@]}"
```

`--model opus…` is appended *after* the `bash -c "…"` string, so under
`bash -c COMMAND [name arg…]` it binds to the outer shell's positional parameters
(`$0`, `$1`), not to `pi`. Unless `pi` is a wrapper that forwards `$@`, the model is
silently dropped. This is identical to `origin/main` (not introduced here), but the
PR rewrites this exact block, so it is worth fixing while in the area — e.g. build
the inner string as `"source ~/.bashrc && pi${MODEL:+ --model $MODEL}"`.

### 4. (Low — pre-existing) Agent `venv` uses `docker exec -it` under non-interactive `init`

The canon `venv` path correctly uses `docker run --rm` with no `-it`. The agent
`venv` path still uses `docker exec -it`, and `init` runs it via `run_command`
(subprocess, no TTY), where `-t` can fail with "the input device is not a TTY".
Unchanged from `origin/main`, but the new canon path highlights the inconsistency;
consider dropping `-it` from the agent `venv` exec too (venv creation is
non-interactive).

### 5. (Nit) Inconsistent shell invocation styles

`hook` uses `bash -lc`, while `agent`/`venv` use `bash -c "source ~/.bashrc && …"`.
Both end up sourcing `~/.bashrc`, so this is harmless, but standardizing (a login
shell already sources bashrc) would remove the redundant explicit `source` and make
the venv-activation contract uniform across subcommands — directly relevant to
Finding 1.

### 6. (Nit) Test coverage gaps

- `test_canon_pre_commit_runs_hook` asserts the `run_command` call but not that the
  audit-log entry (`canon_pre_commit`) is written; the audit write is new behavior
  and untested.
- No negative test for the script's own argument validation (unknown subcommand,
  `--model` on non-`agent`, `--reset` on `shell`/`hook`). Bash-script testing is
  explicitly out of scope per the plan, so this is acceptable to defer, but the
  fail-fast validation is a stated requirement and currently unverified.

## Positives

- Clean single-source-of-truth: container naming/paths removed from Python entirely.
- `_classify_venv_steps` correctly extended to include canon, keeping the init
  resume/preview accurate.
- Helper signatures generalized cleanly (`repo_name: str` instead of `AgentConfig`);
  no stale callers left behind; no dead references to `--shell`/`--venv`/
  `_run_pre_commit_in_sandbox` anywhere in the tree.
- Good inline comments marking the intentional `--env-file` omission and the
  throwaway-container rationale.
- `hook` correctly drops `-it` so a failing hook returns non-zero and surfaces
  through `run_command`.

## Verdict

Mergeable pending the manual Docker verification the plan already calls for, with
Finding 1 as the specific thing to confirm (venv activation in the throwaway `hook`
container). Findings 2–6 are non-blocking; 3 and 4 are pre-existing latent issues
worth cleaning up while this block is being touched.
