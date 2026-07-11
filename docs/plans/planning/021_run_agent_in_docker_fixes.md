# 021: run_agent_in_docker.sh subcommand refactor + canon pre-commit

Input: `intent/017_docker_script` (follow-up), user directive

Completeness: complete

## Goal

Three related changes to `run_agent_in_docker.sh` and its Python integration:

1. **Delegate the builder/reviewer pre-commit run to the script.** `check`
   currently runs the hook via a hand-built `docker exec` in
   `_run_pre_commit_in_sandbox` (workflow.py:134), duplicating container-name and
   path conventions that belong in `run_agent_in_docker.sh`. Move that logic into
   the script so there is a single source of truth for how to enter a sandbox.
2. **Add a dockerized pre-commit run against `canon`.** No container mounts
   `canon`, so a throwaway `docker run --rm` container is spun up to run the hook.
   Exposed as a new standalone `workflow canon_pre_commit` command.
3. **Replace the mode flags with a mandatory subcommand.** The script already has
   `--shell`/`--venv` that completely swap the exec'd command; the new behaviours
   would add more. Replace all of them with a required subcommand in `$1`:
   `agent`, `venv`, `shell`, `hook`.

## Key design decisions (and why)

- **Unified `hook REPO` mechanism (was two subcommands).** Because per-repo
  `.venv` directories are created with `UV_LINK_MODE=copy` and live on the host
  mount, a throwaway `docker run --rm` container that mounts a repo already has a
  warm, self-contained venv. So the *same* code path runs the hook for
  builder/reviewer/canon — no dependence on a live container or its state, no
  `sleep`/"expects existing container" fragility. This replaces the separate
  `pre-commit` (docker exec) and `canon-pre-commit` subcommands with a single
  `hook PROJECT REPO`.
- **`REPO` is always a positional.** With the unified `hook`, canon is just
  another repo value (`hook foo canon`), so there is no special "omit the AGENT
  positional" case.
- **canon gets its own venv, created at init via a throwaway container.** The
  hook runs `black`/`ruff`/`pytest`/`mypy`, all of which live in a per-repo
  `.venv` built by `uv pip install -e .`. `init` only builds venvs for
  `config.agents`; canon is not an agent, so without this `canon_pre_commit`
  would fail on the first hook line. init now also creates a canon venv. Since
  canon has no persistent container, its `venv` run uses `docker run --rm`.
- **Naming.** Script subcommand for running the hook is `hook` (repo-generic).
  The canon-targeting CLI command is `canon_pre_commit`. A single shared Python
  helper `_hook_command(repo_name)` builds every `hook` invocation (both the
  builder pre-commit in `check` and `canon_pre_commit`); it replaces the two
  separately-named helpers proposed earlier — DRY, since both build the same
  subcommand.
- **`hook`/canon-`venv` drop `--env-file`.** The hook and venv creation need no
  API keys; mounting secrets into those throwaway containers is needless attack
  surface. A comment marks where to re-add `--env-file` if ever required.
- **`check` hard-depends on the external script.** After this change `check`
  breaks if `run_agent_in_docker.sh` is absent. Accepted: `init`/`start` already
  depend on it. Documented as a consequence.

## Scope

- `docs/example_run_agent_in_docker.sh` — argument parsing rewrite + `hook` mode +
  canon-aware `venv`.
- `workflow/commands/workflow.py` — command-builder helpers, `_create_venv_command`
  generalised to any repo, canon venv step in init, `_run_pre_commit_hook`, new
  `canon_pre_commit` command.
- `tests/test_workflow_commands.py` — update command-string assertions; add
  `canon_pre_commit` and canon-venv tests.
- `tests/test_integration_workflow.py` — update the two `_run_pre_commit_in_sandbox`
  mock references to the renamed helper.

Out of scope: wiring `canon_pre_commit` into `accept`; changing the hardcoded
image name; branch/clean-tree checks for canon; user documentation (none exists
yet); automated tests of the bash script itself.

## New invocation grammar

```
run_agent_in_docker.sh SUBCOMMAND PROJECT REPO --project-dir DIR [--model MODEL] [--reset]
```

- `$1` = SUBCOMMAND (mandatory), `$2` = PROJECT, `$3` = REPO (all mandatory).
- REPO is an agent name for `agent`/`shell`; an agent name or `canon` for `venv`;
  builder/reviewer/canon for `hook`.

| subcommand | container action                        | command                                                       | --model | --reset |
|------------|-----------------------------------------|---------------------------------------------------------------|:-------:|:-------:|
| `agent`    | run/start persistent `pi-P-R`           | `docker exec … bash -lc "source ~/.bashrc && pi [--model]"`   | yes     | yes     |
| `venv`     | agent: persistent `pi-P-R`; canon: none | agent → `docker exec … uv …`; canon → `docker run --rm … uv …` | no      | yes     |
| `shell`    | expects existing `pi-P-R` (sleep 8)     | `docker exec -it … bash`                                      | no      | no      |
| `hook`     | throwaway `docker run --rm`             | `docker run --rm … bash -lc ./.git-pre-commit-hook`           | no      | no      |

Validation: unknown subcommand → usage error, exit 1. `--model` only valid for
`agent`; `--reset` only for `agent`/`venv`. Passing an inapplicable flag is a
usage error (fail fast), not silently ignored.

## Script changes (`docs/example_run_agent_in_docker.sh`)

### Argument parsing

Replace the `SHELL_MODE`/`VENV_MODE` booleans and `PROJECT="$1"; AGENT="$2"` with:

```bash
[[ $# -ge 3 ]] || usage
SUBCOMMAND="$1"; PROJECT="$2"; REPO="$3"; shift 3
case "$SUBCOMMAND" in
    agent|venv|shell|hook) ;;
    *) echo "Error: unknown subcommand: $SUBCOMMAND" >&2; usage ;;
esac
# then the existing while-loop for --project-dir / --model / --reset
```

After the option loop, reject inapplicable flags (`--model` unless `agent`;
`--reset` unless `agent`/`venv`).

Derived: `CONTAINER="pi-${PROJECT}-${REPO}"`,
`REPO_PATH="/home/auggie/projects/${PROJECT}/${REPO}"`.

### Container bring-up

Run the existing run/start/no-op state machine only for the persistent-container
subcommands: `agent`, `shell`, and `venv` when `REPO != canon`. Skip it entirely
for `hook` and for `venv` when `REPO == canon` (both throwaway). Keep `shell`'s
`sleep 8` + "expects existing container" behaviour.

### Final dispatch

Replace the trailing `if/elif/else` block with `case "$SUBCOMMAND"`:

```bash
case "$SUBCOMMAND" in
    shell)
        exec docker exec -it --user auggie --workdir "$REPO_PATH" "$CONTAINER" bash ;;
    venv)
        [[ -d "${PROJECT_DIR}/${REPO}/.venv" ]] && rm -rf "${PROJECT_DIR}/${REPO}/.venv"
        VENV_CMD="source ~/.bashrc && uv venv --seed && source .venv/bin/activate && uv pip install -e ."
        if [[ "$REPO" == canon ]]; then
            # Throwaway container: canon has no persistent sandbox.
            # NOTE: --env-file intentionally omitted; venv creation needs no secrets.
            exec docker run --rm --user auggie \
                -v "${PROJECT_DIR}/canon:${REPO_PATH}" --workdir "$REPO_PATH" \
                --env UV_VENV_CLEAR=1 --env UV_LINK_MODE=copy \
                gronsti-pi bash -c "$VENV_CMD"
        else
            exec docker exec -it --user auggie --workdir "$REPO_PATH" \
                --env UV_VENV_CLEAR=1 --env UV_LINK_MODE=copy "$CONTAINER" bash -c "$VENV_CMD"
        fi ;;
    hook)
        # Throwaway container; warm .venv comes from the mounted repo dir.
        # NOTE: --env-file intentionally omitted; the hook needs no secrets.
        exec docker run --rm --user auggie \
            -v "${PROJECT_DIR}/${REPO}:${REPO_PATH}" --workdir "$REPO_PATH" \
            gronsti-pi bash -lc "./.git-pre-commit-hook" ;;
    agent)
        EXEC_CMD=(docker exec -it --user auggie --workdir "$REPO_PATH" "$CONTAINER" \
            bash -c "source ~/.bashrc && pi")
        [[ -n "$MODEL" ]] && EXEC_CMD+=(--model "$MODEL")
        exec "${EXEC_CMD[@]}" ;;
esac
```

Notes:
- `hook` drops `-it` (non-interactive; runs under `workflow check`/`canon_pre_commit`),
  so a failing hook returns non-zero and `run_command` surfaces it.
- Update the file header/usage comment to document the subcommand grammar.

## Python integration (`workflow/commands/workflow.py`)

### Command-builder helpers

```python
def _agent_docker_command(script, project_name, project_dir, agent, *, reset) -> str:
    parts = [script, "agent", project_name, agent.name, "--project-dir", str(project_dir)]
    if agent.model: parts += ["--model", agent.model]
    if reset: parts.append("--reset")
    return shlex.join(parts)

def _builder_shell_command(script, project_name, project_dir) -> str:
    return shlex.join([script, "shell", project_name, "builder", "--project-dir", str(project_dir)])

def _create_venv_command(script, project_name, project_dir, repo_name: str) -> list[str]:
    return [script, "venv", project_name, repo_name, "--project-dir", str(project_dir), "--reset"]

def _hook_command(script, project_name, project_dir, repo_name: str) -> list[str]:
    return [script, "hook", project_name, repo_name, "--project-dir", str(project_dir)]
```

`_create_venv_command` now takes a repo-name string instead of `AgentConfig`
(callers pass `agent.name` or `"canon"`). `_hook_command` is the single builder
for every `hook` invocation.

### init: create the canon venv

After the existing agent venv loop (workflow.py:380), add:

```python
run_command(_create_venv_command(script, project_name, project_dir, RepoName.canon),
            cwd=project_dir, dry_run=dry_run)
```

(Update `_classify_init_steps` if it enumerates venv steps, so the resume/preview
reflects the canon venv too.)

### `_run_pre_commit_in_sandbox` → `_run_pre_commit_hook`

Rewrite to shell out via `_hook_command`; the old `pi-{project}-{repo}` name and
`/home/auggie/...` path leave Python entirely:

```python
def _run_pre_commit_hook(config, repo_name, *, dry_run) -> None:
    project_dir = _require_project_dir(config)
    project_name = config.project_name or project_dir.name
    script = _find_docker_script(project_dir)
    run_command(_hook_command(script, project_name, project_dir, repo_name), dry_run=dry_run)
```

The old `RepoName.canon` guard is removed — `hook` now supports canon. Update the
call site in `check` and the two mock references in `tests/test_integration_workflow.py`.

### New `canon_pre_commit` command

Standalone script command (auto-registered as `workflow canon_pre_commit` via the
`_SCRIPT_REGISTRY` fallback in `cli.py`, like `status`/`doctor`):

```python
@register_script("canon_pre_commit")
@click.pass_context
def canon_pre_commit(ctx: click.Context) -> None:
    """Run the pre-commit hook against canon in a throwaway container."""
    config = ctx.obj["config"]; dry_run = ctx.obj["dry_run"]
    try:
        _run_pre_commit_hook(config, RepoName.canon, dry_run=dry_run)
        _write_script_audit_log(config, "canon_pre_commit", dry_run=dry_run)
    except (subprocess.CalledProcessError, WorkflowError) as exc:
        print_workflow_error(exc); ctx.exit(1); return
    ctx.exit(0)
```

## Verification

- `uv run pytest -q` and `uv run mypy workflow tests` green.
- Update `test_start_*` / `_create_venv` assertions (test_workflow_commands.py:104–232)
  to the new order, e.g. `f"{script} agent {project} planner --project-dir {d} --model opus4.6"`,
  `f"{script} shell {project} builder --project-dir {d}"`, and the venv commands to
  `venv {project} <repo>`.
- `test_init_*`: assert a canon venv command (`venv {project} canon`) is issued in
  addition to the per-agent ones.
- Update `test_check_runs_pre_commit_and_prints_review_reminder` (line 414): the
  first `run_command` call is now `[<script>, "hook", <project>, "builder", "--project-dir", …]`,
  not `["docker", "exec", …]`. (This test currently fails on HEAD — its assertion
  was not updated when the docker-exec command last changed; this fixes it.)
- Add `test_canon_pre_commit_runs_hook`: invoke `workflow canon_pre_commit`, assert
  `run_command` received `[<script>, "hook", <project>, "canon", "--project-dir", <d>]`.
- Manual (sandbox can't run Docker): copy the example into a real project; run each
  subcommand once; confirm `hook`/canon-`venv` containers are auto-removed (`docker ps -a`).

## Risks

1. **`check` requires the external script present.** If `run_agent_in_docker.sh`
   is not at the project root or on `PATH`, `_find_docker_script` returns a bare
   name and `check` fails when it executes. Accepted (init/start already depend on
   it); consider having `_find_docker_script` error loudly when absent rather than
   defer to an obscure exec failure.
2. **canon venv must exist before `canon_pre_commit`.** The command is only
   functional after `init` has built canon's `.venv`. Re-running `init` (with
   `--continue`) refreshes it; a project initialised before this change needs a
   one-off `run_agent_in_docker.sh venv <project> canon --project-dir <d> --reset`.
3. **uid/permissions on the canon mount.** The throwaway container runs as
   `--user auggie` and writes `.venv`/caches into the host-mounted repo; this
   relies on the same uid alignment the agent containers already assume.
4. **Hardcoded image name** (`gronsti-pi`) now appears in the agent-run, canon-venv,
   and hook blocks of the template; a customiser must change all occurrences.

## Dependencies

Builds on `done/017_docker_script`. No blocking dependencies on unimplemented plans.
