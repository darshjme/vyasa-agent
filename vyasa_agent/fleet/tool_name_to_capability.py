# SPDX-License-Identifier: Apache-2.0
"""Tool-name to :class:`Capability` mapping.

The hermes tool registry names tools by string (e.g. ``run_bash``,
``web_fetch``, ``image_gen``). The capability matrix is indexed by the
:class:`Capability` enum. This module is the single source of truth that
bridges the two so Layer A (boot filter) and Layer B (pre-tool hook) can
answer "which capability does this tool fall under?" in one call.

Any tool name not present in :data:`TOOL_TO_CAPABILITY` maps to
:attr:`Capability.UNKNOWN`, which always evaluates to
:attr:`Decision.DENY` via :meth:`CapabilityMatrix.check`.
"""

from __future__ import annotations

from .capability import Capability

# 25 common hermes tool names, grouped by capability. Extend this table as
# new tools land in the registry; the test suite asserts no capability is
# silently orphaned.
TOOL_TO_CAPABILITY: dict[str, Capability] = {
    # filesystem
    "file_read": Capability.FS_READ,
    "read_file": Capability.FS_READ,
    "list_dir": Capability.FS_READ,
    "file_write": Capability.FS_WRITE,
    "write_file": Capability.FS_WRITE,
    "edit_file": Capability.FS_WRITE,
    # shell
    "run_bash": Capability.BASH,
    "bash": Capability.BASH,
    "shell": Capability.BASH,
    # source control
    "git": Capability.GIT,
    "git_commit": Capability.GIT,
    "git_push": Capability.GIT,
    # containers
    "docker": Capability.DOCKER,
    "docker_build": Capability.DOCKER,
    # network
    "web_fetch": Capability.WEB_FETCH,
    "web_search": Capability.WEB_SEARCH,
    # gateways
    "tg_send": Capability.GATEWAY_TELEGRAM,
    "telegram_send": Capability.GATEWAY_TELEGRAM,
    "wa_send": Capability.GATEWAY_WHATSAPP,
    "whatsapp_send": Capability.GATEWAY_WHATSAPP,
    "slack_send": Capability.GATEWAY_SLACK,
    # orchestration
    "delegate": Capability.DELEGATE,
    "spawn_subagent": Capability.SPAWN_SUBAGENT,
    # execution
    "code_exec": Capability.CODE_EXEC,
    "python_exec": Capability.CODE_EXEC,
    # perception
    "vision": Capability.VISION,
    "image_describe": Capability.VISION,
    "stt": Capability.STT,
    "speech_to_text": Capability.STT,
    "tts": Capability.TTS,
    "text_to_speech": Capability.TTS,
    # security
    "pentest": Capability.PENTEST,
    "nmap": Capability.PENTEST,
    "semgrep": Capability.PENTEST,
    "trivy": Capability.PENTEST,
    # commerce
    "envato_verify": Capability.ENVATO_VERIFY,
    # generative
    "image_gen": Capability.IMAGE_GEN,
    # data
    "data_pipelines": Capability.DATA_PIPELINES,
    "data_pipeline_run": Capability.DATA_PIPELINES,
}


def lookup(tool_name: str) -> Capability:
    """Return the :class:`Capability` for a tool name, or ``UNKNOWN``.

    ``UNKNOWN`` is always default-deny downstream, so unmapped tools cannot
    bypass the matrix simply by existing.
    """
    return TOOL_TO_CAPABILITY.get(tool_name, Capability.UNKNOWN)


__all__ = ["TOOL_TO_CAPABILITY", "lookup"]
