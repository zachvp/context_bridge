# Contributing to Context Bridge

Thanks for your interest. Context Bridge is a focused tool — contributions that
improve correctness, reliability, or usability are most welcome.

## Development setup

**Prerequisites:** Python 3.13+, Claude Code CLI

```bash
git clone https://github.com/zachvp/context_bridge.git
cd context_bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

No database is required to run the test suite.

## Running tests

```bash
pytest                              # unit tests
bash tests/check_docs.sh           # structural lint (versions, file paths)
```

Smoke tests require a built database and are run manually — see `README.md` for
instructions.

## Code style

- Standard library only in core modules (`ingest.py`, `query.py`, `embed.py`,
  `server.py`); third-party imports belong in the module that owns them
- Type hints on all public functions
- No comments that restate what the code does — only add one when the *why* is
  non-obvious

There is no auto-formatter enforced yet; match the style of the surrounding code.

## Submitting changes

1. Fork the repository and create a branch from `main`
2. Make your changes with tests where applicable
3. Run `pytest` and `bash tests/check_docs.sh` — both must pass
4. Open a pull request against `main` with a clear description of what changed
   and why

For significant changes (new features, schema changes, new dependencies) open an
issue first to discuss the approach before writing code.

## Known roadmap gaps

See `PLAN.md` for Phase 2 and Phase 3 items that are explicitly planned but not
yet implemented. Contributions toward those phases are welcome — please open an
issue to coordinate before starting.

## License

By contributing you agree that your changes will be licensed under the MIT
License that covers this project.
