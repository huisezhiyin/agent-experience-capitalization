# Contributing

Thanks for helping improve Agent Experience Capitalization.

This project is still in an early prototype phase. The most valuable
contributions are small, well-tested changes that make the runtime easier to
use, easier to explain, or safer to run in public repositories.

## Development Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[milvus]"
python3 -m unittest discover -s tests -v
```

If you do not need Milvus Lite locally, install without extras:

```bash
.venv/bin/pip install -e .
```

## Contribution Guidelines

- Keep local runtime state out of commits. `.agent-memory/`, SQLite files, and
  Milvus DB files are intentionally ignored.
- Prefer small pull requests with a clear behavior change and a test.
- Do not include private paths, personal emails, API keys, logs, or generated
  agent traces in issues or pull requests.
- Preserve the local-first behavior. Optional cloud or vector backends should
  be additive and configurable.
- When changing activation or retrieval behavior, include evidence from tests
  and, when useful, a short activation smoke result.

## Pull Request Checklist

- Tests pass with `python3 -m unittest discover -s tests -v`.
- Public docs are updated when user-facing behavior changes.
- New files do not contain local private information.
- The change is compatible with the current CLI workflow.

## License

By contributing, you agree that your contributions are licensed under the
Apache License 2.0.
