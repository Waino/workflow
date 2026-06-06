This file contains a proof-of-concept for the tmux pane rotation,
to be used in the `workflow go` subcommand (and possibly others as well).

It has been manually tested, and confirmed to work.

```python
#!/usr/bin/env python
import click
import sys
import libtmux

server = libtmux.Server()
SESSION_NAME = "panerot-demo"

# Modification vs original POC: record the control pane's id in a session user option at setup,
# then locate the control pane by that id in `go` (instead of assuming it is the active pane
# at index 0 of the active window). Pane ids are stable across join-pane, so the recorded id
# stays valid across rotations.
CONTROL_PANE_OPTION = "@workflow_control_pane"


@click.group()
def cli():
    pass


@cli.command()
def setup():
    session = server.new_session(SESSION_NAME, x=255)

    planner_window = session.active_window
    planner_window.rename_window("planner")
    planner_window.active_pane.send_keys('echo "This is the planner"', enter=True)

    window = session.new_window(window_name="builder", attach=False)
    window.active_pane.send_keys('echo "This is the builder"', enter=True)

    window = session.new_window(window_name="reviewer", attach=False)
    window.active_pane.send_keys('echo "This is the reviewer"', enter=True)

    window = session.new_window(window_name="manual", attach=False)
    window.active_pane.send_keys('echo "This is the manual shell into builder"', enter=True)

    pane = planner_window.split(
        attach=True,
        direction=libtmux.pane.PaneDirection.Left,
    )
    pane.send_keys('echo "This is the control"', enter=True)

    # Modification: remember which pane is the control pane.
    session.set_option(CONTROL_PANE_OPTION, pane.pane_id)
    print(f"Recorded control pane {pane.pane_id} in option {CONTROL_PANE_OPTION}")

    session.attach()
    # cleanup demo
    try:
        session.kill()
    except Exception:
        pass


@cli.command()
@click.argument("target", type=click.Choice(["planner", "builder", "reviewer", "manual"]))
def go(target):
    if not server.has_session(SESSION_NAME, exact=True):
        print(f"No session {SESSION_NAME}")
        return 1
    session = server.sessions.get(session_name__exact=SESSION_NAME)

    # Modification: locate the control pane by its recorded id instead of by active-pane/index.
    control_pane_id = session.show_option(CONTROL_PANE_OPTION)
    if not control_pane_id:
        print(f"No control pane recorded in {CONTROL_PANE_OPTION}; run setup first")
        return 1
    control_pane = server.panes.get(pane_id=control_pane_id, default=None)
    if control_pane is None:
        print(f"Recorded control pane {control_pane_id} no longer exists")
        return 1
    source_window = control_pane.window

    target_window = session.windows.get(window_name__exact=target)
    if source_window == target_window:
        print("Control pane already in target window")
        return 0

    print(f"Everything ok, moving control pane {control_pane_id} to {target_window.name}")
    # join horizontally (-h), before (-b); source is the control pane id (stable across moves)
    cmd = ["join-pane", "-h", "-b", "-s", control_pane_id, "-t", f":{target_window.name}"]
    print(cmd)
    res = session.cmd(*cmd)
    print(res.stdout)
    print(res.stderr)


if __name__ == "__main__":
    sys.exit(cli())  # pragma: no cover
```
