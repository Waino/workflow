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
    source_window = session.active_window
    pane = source_window.active_pane

    if not len(source_window.panes) == 2:
        print(f"Expecting source window to have 2 panes, found {len(source_window.panes)}")
        return 1
    if not source_window.panes[0] == pane:
        print("Expecting the first pane to be active")
        return 1

    target_window = session.windows.get(window_name__exact=target)
    if source_window == target_window:
        print("Source and target are the same")
        return 1
    if not len(target_window.panes) == 1:
        print(f"Expecting target window to have 1 pane, found {len(target_window.panes)}")
        return 1

    print(f"Everything ok, moving from {source_window.name} to {target_window.name}")
    # join horizontally (-h), before (-b)
    cmd = ["join-pane", "-h", "-b", "-s", f":{source_window.name}.0", "-t", f":{target_window.name}"]
    print(cmd)
    res = session.cmd(*cmd)
    print(res.stdout)
    print(res.stderr)


if __name__ == "__main__":
    sys.exit(cli())  # pragma: no cover
```
