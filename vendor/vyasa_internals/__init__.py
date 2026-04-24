"""Vendored runtime surface for the Vyasa Agent fleet.

This package is the white-labelled, trimmed re-export of the upstream
runtime. Only the symbols the Vyasa fleet actually consumes are exposed;
everything else stays as an implementation detail inside a submodule so
callers don't grow accidental coupling.

Public surface (Phase-1 Duo):

    AIAgent               - runtime shell, signature-compatible
    IterationBudget       - tool-call budget counter
    run_conversation      - module-level convenience wrapper

    get_tool_definitions  - filtered list of tool schemas
    set_session_context   - bind a session id to the current thread
    clear_session_context - drop the bound session id
    setup_logging         - rotating-file logger setup

    get_vyasa_home        - resolve the VYASA_HOME directory
    get_config_path       - resolve <VYASA_HOME>/config.yaml
    get_skills_dir        - resolve <VYASA_HOME>/skills

    SessionDB             - minimal session persistence shell

    now                   - timezone-aware wall-clock helper

Origin and licence attribution live in NOTICE.md alongside this file and
in the top-level NOTICE at the repo root.
"""

from __future__ import annotations

from vyasa_internals.agent_runtime import (
    AIAgent,
    IterationBudget,
    run_conversation,
)
from vyasa_internals.constants import (
    AI_GATEWAY_BASE_URL,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODELS_URL,
    apply_ipv4_preference,
    display_vyasa_home,
    get_config_path,
    get_default_vyasa_root,
    get_env_path,
    get_optional_skills_dir,
    get_skills_dir,
    get_subprocess_home,
    get_vyasa_dir,
    get_vyasa_home,
    is_container,
    is_termux,
    is_wsl,
    parse_reasoning_effort,
)
from vyasa_internals.logging_utils import (
    clear_session_context,
    set_session_context,
    setup_logging,
    setup_verbose_logging,
)
from vyasa_internals.model_tools import (
    check_toolset_requirements,
    get_tool_definitions,
    get_toolset_for_tool,
)
from vyasa_internals.state import DEFAULT_DB_PATH, SessionDB
from vyasa_internals.time_utils import get_timezone, now
from vyasa_internals.toolsets import resolve_toolset, validate_toolset

__all__ = [
    # Agent runtime
    "AIAgent",
    "IterationBudget",
    "run_conversation",
    # Constants / path helpers
    "AI_GATEWAY_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODELS_URL",
    "apply_ipv4_preference",
    "display_vyasa_home",
    "get_config_path",
    "get_default_vyasa_root",
    "get_env_path",
    "get_optional_skills_dir",
    "get_skills_dir",
    "get_subprocess_home",
    "get_vyasa_dir",
    "get_vyasa_home",
    "is_container",
    "is_termux",
    "is_wsl",
    "parse_reasoning_effort",
    # Logging
    "clear_session_context",
    "set_session_context",
    "setup_logging",
    "setup_verbose_logging",
    # Tools + toolsets
    "check_toolset_requirements",
    "get_tool_definitions",
    "get_toolset_for_tool",
    "resolve_toolset",
    "validate_toolset",
    # Session state
    "DEFAULT_DB_PATH",
    "SessionDB",
    # Time
    "get_timezone",
    "now",
]
