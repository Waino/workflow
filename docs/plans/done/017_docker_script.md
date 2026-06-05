# 017: Docker Shell Script for Agent Launch

Input: `intent/017_docker_script`

## Goal

1. Replace the three `# TODO: placeholder` lines in `workflow start` with real
   invocations of `run_agent_in_docker.sh`, and add a `--reset` flag to
   `workflow start` that is passed through to the script for agent windows.
2. Provide a ready-to-copy example shell script so that each project only needs
   to drop in a customised version rather than write one from scratch.

## Scope

- `workflow/commands/workflow.py` — `start` command only
- `tests/test_workflow_commands.py` — update and extend `test_start_*`
- `docs/example_run_agent_in_docker.sh` — bare-bones example (new file)

## Changes to `workflow/commands/workflow.py`

### 1. Add `_find_docker_script` helper

```python
def _find_docker_script(project_dir: Path) -> str:
    """Return the path to run_agent_in_docker.sh, or just its name for PATH lookup."""
    candidate = project_dir / "run_agent_in_docker.sh"
    if candidate.is_file():
        return str(candidate)
    return "run_agent_in_docker.sh"
```

Pure function; easy to unit-test in isolation.

### 2. Add `_agent_docker_command` helper

```python
def _agent_docker_command(
    script: str,
    project_name: str,
    project_dir: Path,
    agent: AgentConfig,
    *,
    reset: bool,
) -> str:
    parts = [script, project_name, agent.name, "--project-dir", str(project_dir), "--model", agent.model]
    if reset:
        parts.append("--reset")
    return shlex.join(parts)
```

`shlex.join` handles any spaces in paths or names safely.

### 3. Add `_builder_shell_command` helper

```python
def _builder_shell_command(script: str, project_name: str, project_dir: Path) -> str:
    return shlex.join([script, project_name, "builder", "--project-dir", str(project_dir), "--shell"])
```

### 4. Add `--reset` option to `start`

```python
@register_script("start")
@click.option("--reset", is_flag=True, default=False,
              help="Remove existing containers instead of restarting.")
@click.pass_context
def start(ctx: click.Context, *, reset: bool) -> None:
```

### 5. Replace the three `# TODO: placeholder` lines

Inside `start`, after `_require_project_dir`:

```python
project_dir = _require_project_dir(config)
script = _find_docker_script(project_dir)
project_name = config.project_name or project_dir.name

first_agent = config.agents[0]
first_command = _agent_docker_command(script, project_name, project_dir, first_agent, reset=reset)
run_command(
    ["tmux", "new-session", "-d", "-s", config.tmux.session_name,
     "-n", first_agent.name, first_command],
    dry_run=dry_run,
)
for agent in config.agents[1:]:
    run_command(
        ["tmux", "new-window", "-t", config.tmux.session_name,
         "-n", agent.name, _agent_docker_command(script, project_name, project_dir, agent, reset=reset)],
        dry_run=dry_run,
    )
run_command(
    ["tmux", "new-window", "-t", config.tmux.session_name,
     "-n", "builder-shell", _builder_shell_command(script, project_name, project_dir)],
    dry_run=dry_run,
)
```

Note: `_require_project_dir` already raises `WorkflowError` when `project_dir`
is `None`, so the returned value is guaranteed to be a `Path`.

## Changes to `tests/test_workflow_commands.py`

### Update `test_start_runs_tmux_commands`

Extend the existing test to assert on the actual command strings, not just the
tmux window names. The test uses `_make_project` which copies
`docs/default_config.yaml` (agents: planner/opus4.6, builder/sonnet4.6,
reviewer/gpt5.4). No `run_agent_in_docker.sh` exists in `tmp_path`, so the
script name falls back to the bare string.

```python
def test_start_runs_tmux_commands(...):
    ...
    # existing window-name assertions stay
    assert calls[0][:6] == ["tmux", "new-session", "-d", "-s", "workflow", "-n"]
    assert calls[0][6] == "planner"
    assert [call[5] for call in calls[1:3]] == ["builder", "reviewer"]
    assert calls[3][5] == "builder-shell"

    # new: verify command strings
    expected_script = "run_agent_in_docker.sh"
    project = tmp_path.name
    project_dir = str(tmp_path.resolve())
    assert calls[0][7] == f"{expected_script} {project} planner --project-dir {project_dir} --model opus4.6"
    assert calls[1][6] == f"{expected_script} {project} builder --project-dir {project_dir} --model sonnet4.6"
    assert calls[2][6] == f"{expected_script} {project} reviewer --project-dir {project_dir} --model gpt5.4"
    assert calls[3][6] == f"{expected_script} {project} builder --project-dir {project_dir} --shell"
```

### Add `test_start_reset_flag_threads_through`

```python
def test_start_reset_flag_threads_through(...):
    """--reset is appended to agent commands but NOT to builder-shell."""
    ...
    result = CliRunner().invoke(main, ["--config", str(config_path), "start", "--reset"])
    assert result.exit_code == 0
    calls = [c.args[0] for c in fake_run_command.call_args_list]
    # agent commands end with --reset
    assert calls[0][7].endswith("--reset")  # first agent (new-session)
    assert calls[1][6].endswith("--reset")  # second agent (new-window)
    assert calls[2][6].endswith("--reset")  # third agent (new-window)
    # builder-shell does NOT have --reset
    assert "--reset" not in calls[3][6]
```

### Add `test_start_uses_project_local_script_when_present`

```python
def test_start_uses_project_local_script_when_present(...):
    """Script in project_dir takes priority over PATH."""
    config_path = _make_project(tmp_path)
    local_script = tmp_path / "run_agent_in_docker.sh"
    local_script.touch()
    monkeypatch.chdir(tmp_path)
    fake_run_command = MagicMock()
    monkeypatch.setattr(wc, "run_command", fake_run_command)

    CliRunner().invoke(main, ["--config", str(config_path), "start"])

    calls = [c.args[0] for c in fake_run_command.call_args_list]
    assert calls[0][7].startswith(str(tmp_path))  # absolute path used
```

## Type annotations

- `_find_docker_script(project_dir: Path) -> str`
- `_agent_docker_command(script: str, project_name: str, project_dir: Path, agent: AgentConfig, *, reset: bool) -> str`
- `_builder_shell_command(script: str, project_name: str, project_dir: Path) -> str`

`AgentConfig` is already imported via `WorkflowConfig`; add an explicit import
if mypy requires it (it is in `workflow.config`).

## `docs/example_run_agent_in_docker.sh`

A bare-bones, copy-and-customise example installed at
`docs/example_run_agent_in_docker.sh`. Key implementation notes:

- Accepts `--project-dir DIR` (required option) so that volume mounts and
  `env.list` discovery are correct whether the script lives in the project root
  or anywhere on `$PATH`. `workflow start` always passes the absolute `project_dir`.
- Validates that `--project-dir` was supplied and that the directory exists, then
  checks that `${PROJECT_DIR}/env.list` exists and exits with a clear message if
  not, with a reminder to add API keys there.
- Includes a commented `--env` example in the `docker run` block reminding
  operators to pass per-agent secrets that should not live in `env.list`.
- Argument parsing handles `--model`, `--reset`, `--shell` in a `while` loop;
  `--model` is optional (omitted from the exec if not provided).
- Container state is detected via `docker inspect --format '{{.State.Status}}'`;
  unknown states exit with an error rather than silently misbehaving.
- Uses `exec` for the final `docker exec` so that the shell process is replaced
  and signal handling works correctly inside tmux.

## Checklist

- [ ] Create `docs/example_run_agent_in_docker.sh`
- [ ] Add `_find_docker_script` helper
- [ ] Add `_agent_docker_command` helper
- [ ] Add `_builder_shell_command` helper
- [ ] Add `--reset` option to `start`
- [ ] Replace three `# TODO: placeholder` lines
- [ ] Update `test_start_runs_tmux_commands` to assert on command strings
- [ ] Add `test_start_reset_flag_threads_through`
- [ ] Add `test_start_uses_project_local_script_when_present`
- [ ] `uv run mypy workflow tests` passes
- [ ] `uv run pytest -q` passes
