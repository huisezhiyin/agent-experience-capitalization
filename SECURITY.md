# Security Policy

## Supported Versions

The project is currently pre-1.0. Security fixes are handled on the latest
`main` branch unless a stable release branch is introduced later.

## Reporting a Vulnerability

Please report vulnerabilities privately through GitHub Security Advisories if
available on the repository. If advisories are not available, open a minimal
public issue that says you have a potential vulnerability to report, but do
not include exploit details, secrets, private logs, or private file paths.

Useful reports include:

- A short description of the impact.
- Steps to reproduce with synthetic data.
- Affected commands or files.
- Whether local runtime state, generated memory, or vector indexes are exposed.

## Security Scope

This project writes local runtime state under `.agent-memory/` and may use
SQLite or Milvus Lite files. These files can contain task summaries, generated
experience assets, activation logs, and other agent context. Treat them as
local private data unless you intentionally sanitize and publish them.

Do not commit:

- `.agent-memory/`
- SQLite or Milvus database files
- API keys, tokens, private logs, or personal paths
- Unsanitized generated agent traces
