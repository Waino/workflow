# Project Guidelines

Always start by reading docs/plans/intent/001_idea.md

## Code Style
- black
- Maximum line length 120.
- Always use type annotations.
- Follow the existing naming conventions in the codebase.
- Keep it simple and concise. Avoid intoducing unnecessary complexity. Instead favor short, simple, and readable solutions. Find a good balance between DRY (Don't repeat yourself) and avoidance of overengineering.
- Write testable code. If it is possible to extract logic into a pure function, prefer refactoring to use the pure function instead of mixing logic with side effects.

## Git usage
- Commit your changes, but don't push.
- Use semantic commit messages following conventional commits.
- Use feature branches, stop and let me know when a feature is ready to be merged.

## Python development
- Use uv for virtual environment and dependency management.
- Use flit for python packaging.
- There is a git pre commit hook in the file .git-pre-commit-hook. Install it by symlinking to `.git/hooks/pre-commit` and use it. It runs black, pytest, and mypy. These three should always run successfully for each commit. Note that initially running `pytest` has been temporarily disabled. Enable it once the first test has been written.
- DO NOT use `git commit --no-verify`. Stop and ask for instructions instead of omitting the pre-commit hook.

## Sandboxed environment
- Note that you run in a sandboxed and firewalled environment.
- Your Internet use is restricted: don't try to download or upload anything.
- You can install Python packages from pypi, but you can not download or install CLI tools.
- If it is necessary for completing the work, you can stop and propose other tools to install. Do not try to install anything directly.
- Note that you are not allowed to use Docker commands.

## Documentation
- Write user documentation in the `docs` subdirectory, in ReStructuredText format.
- Update documentation when adding new features.
