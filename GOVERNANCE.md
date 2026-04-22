# Governance

This project currently uses lightweight maintainer governance.

## Maintainer Responsibilities

Maintainers are responsible for:

- Reviewing pull requests and issues.
- Keeping public docs accurate.
- Protecting user privacy in examples, tests, and generated artifacts.
- Preserving the local-first design unless a change is explicitly optional.
- Making sure retrieval and activation behavior remains explainable.

## Decision Making

For now, decisions are made by maintainer consensus in issues and pull
requests. Significant design changes should be documented in the pull request
description or a short design issue before implementation.

Examples of significant changes:

- Changing the default storage backend.
- Adding a required external service.
- Changing activation ranking semantics.
- Introducing a new public data format.

## Project Status

The project is pre-1.0. APIs, schemas, and command behavior may still change,
but changes should be tested and documented.
