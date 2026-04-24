# Changelog

All notable changes to Vyasa Agent are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial repository scaffold: Apache-2.0 LICENSE, NOTICE for upstream credits, Graymatter contributor constitution, pyproject with 15 core deps and 5 extras.
- Partner constitution codifying white-label, Graphify-first, and CodeCanyon-grade directives.
- Directory layout: `vyasa_agent/{fleet,gateway,graphify,admin_panel}`, `employees/`, `plans/`, `plugins/`, `skills/`, `docs/`, `scripts/`, `assets/`, `tests/`.

## [0.1.0] — TBD

### Planned
- Fleet manager with in-process AIAgent actors, 28-employee roster.
- Telegram + WhatsApp gateway adapters with per-employee routing.
- Graphify v2 knowledge store (SQLite + WAL + Qdrant namespace).
- Capability matrix enforcement (boot filter + runtime hook + audit log).
- Admin panel FastAPI + React scaffold with settings-backed zero-hardcoding.
- 1-click installer for macOS launchd, Linux systemd-user, Docker Compose, Fly.io.
