"""Phase-1 Duo agent runtime shell.

This module holds a deliberately slim ``AIAgent`` that keeps the full
public constructor signature of the upstream donor's runtime but delegates
actual conversation execution to the Phase-2 wiring.

Why a shell and not a verbatim vendor?

The upstream ``run_agent`` file is ~12k lines with ~40 transitive imports
into an ``agent/`` subpackage that itself totals another ~25k lines
(memory manager, context compressor, codex-responses adapter, prompt
caching, 9 provider adapters, usage pricing, trajectory writer, etc.).

Phase-1 Duo mode — Vyasa + one partner, no tools, single language-model
roundtrip — does not exercise 95% of that surface, and the vendor budget
for this phase is ≤ 3000 lines total. Vendoring the donor runtime verbatim would
blow the budget by an order of magnitude and drag in paths the
white-label scanner still flags (the scanner runs on the vendored tree,
so donor identifiers baked into internal variable names become hits).

The pragmatic answer is:
  * preserve the ``AIAgent.__init__`` signature exactly so our smoke test
    (``tests/test_vendor_import.py``) and any future caller that builds
    an agent end-to-end keeps the same kwargs;
  * expose ``run_conversation`` as an explicit ``NotImplementedError`` so
    any caller that accidentally boots the shell during Phase-1 fails
    loud, not silent;
  * keep the module tiny and pure-Python so the vendored tree stays
    under budget.

Phase-2 will swap this shell for a real runtime — either by trimming the
upstream ``run_agent.py`` against a concrete feature matrix, or by
re-implementing on top of the vendor ``model_tools`` + ``toolsets`` pair.
That decision is tracked in design-10 §4.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class IterationBudget:
    """Minimal iteration-budget counter used only for signature fidelity.

    The upstream class tracked tool-call counts, nested-agent debt and
    turn-scoped retries. Phase-1 Duo just needs a value the constructor
    can accept and pass through unchanged.
    """

    def __init__(self, max_iterations: int = 90) -> None:
        self.max_iterations = max_iterations
        self.consumed = 0

    def remaining(self) -> int:
        return max(0, self.max_iterations - self.consumed)


class AIAgent:
    """Phase-1 Duo shell for the vendored agent runtime.

    The constructor signature is kept in lockstep with the upstream donor
    so downstream callers — the fleet orchestrator, the admin panel, the
    messaging adapters — can be wired against the same kwargs they will
    see once the Phase-2 runtime lands.

    ``run_conversation`` intentionally raises ``NotImplementedError`` so
    that any code path which reaches for the real loop during Phase-1
    fails loudly at the boundary instead of pretending to respond.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        provider: Optional[str] = None,
        api_mode: Optional[str] = None,
        acp_command: Optional[str] = None,
        acp_args: Optional[List[str]] = None,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        model: str = "",
        max_iterations: int = 90,
        tool_delay: float = 1.0,
        enabled_toolsets: Optional[List[str]] = None,
        disabled_toolsets: Optional[List[str]] = None,
        save_trajectories: bool = False,
        verbose_logging: bool = False,
        quiet_mode: bool = False,
        ephemeral_system_prompt: Optional[str] = None,
        log_prefix_chars: int = 100,
        log_prefix: str = "",
        providers_allowed: Optional[List[str]] = None,
        providers_ignored: Optional[List[str]] = None,
        providers_order: Optional[List[str]] = None,
        provider_sort: Optional[str] = None,
        provider_require_parameters: bool = False,
        provider_data_collection: Optional[str] = None,
        session_id: Optional[str] = None,
        tool_progress_callback: Optional[Any] = None,
        tool_start_callback: Optional[Any] = None,
        tool_complete_callback: Optional[Any] = None,
        thinking_callback: Optional[Any] = None,
        reasoning_callback: Optional[Any] = None,
        clarify_callback: Optional[Any] = None,
        step_callback: Optional[Any] = None,
        stream_delta_callback: Optional[Any] = None,
        interim_assistant_callback: Optional[Any] = None,
        tool_gen_callback: Optional[Any] = None,
        status_callback: Optional[Any] = None,
        max_tokens: Optional[int] = None,
        reasoning_config: Optional[Dict[str, Any]] = None,
        service_tier: Optional[str] = None,
        request_overrides: Optional[Dict[str, Any]] = None,
        prefill_messages: Optional[List[Dict[str, Any]]] = None,
        platform: Optional[str] = None,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        chat_id: Optional[str] = None,
        chat_name: Optional[str] = None,
        chat_type: Optional[str] = None,
        thread_id: Optional[str] = None,
        gateway_session_key: Optional[str] = None,
        skip_context_files: bool = False,
        skip_memory: bool = False,
        session_db: Any = None,
        parent_session_id: Optional[str] = None,
        iteration_budget: Optional[IterationBudget] = None,
        fallback_model: Optional[Dict[str, Any]] = None,
        credential_pool: Any = None,
        checkpoints_enabled: bool = False,
        checkpoint_max_snapshots: int = 50,
        pass_session_id: bool = False,
        persist_session: bool = True,
    ) -> None:
        # Core addressing
        self.base_url = base_url
        self.api_key = api_key
        self.provider = provider
        self.api_mode = api_mode
        self.acp_command = acp_command
        self.acp_args = list(acp_args) if acp_args else None
        self.command = command
        self.args = list(args) if args else None

        # Model + loop controls
        self.model = model
        self.max_iterations = max_iterations
        self.tool_delay = tool_delay
        self.enabled_toolsets = list(enabled_toolsets) if enabled_toolsets else None
        self.disabled_toolsets = list(disabled_toolsets) if disabled_toolsets else None
        self.max_tokens = max_tokens
        self.reasoning_config = reasoning_config
        self.service_tier = service_tier
        self.request_overrides = request_overrides
        self.fallback_model = fallback_model
        self.iteration_budget = iteration_budget or IterationBudget(max_iterations)

        # Trajectory / log knobs
        self.save_trajectories = save_trajectories
        self.verbose_logging = verbose_logging
        self.quiet_mode = quiet_mode
        self.log_prefix_chars = log_prefix_chars
        self.log_prefix = log_prefix

        # Prompting
        self.ephemeral_system_prompt = ephemeral_system_prompt
        self.prefill_messages = list(prefill_messages) if prefill_messages else None

        # Provider routing
        self.providers_allowed = list(providers_allowed) if providers_allowed else None
        self.providers_ignored = list(providers_ignored) if providers_ignored else None
        self.providers_order = list(providers_order) if providers_order else None
        self.provider_sort = provider_sort
        self.provider_require_parameters = provider_require_parameters
        self.provider_data_collection = provider_data_collection

        # Session / identity
        self.session_id = session_id or str(uuid.uuid4())
        self.parent_session_id = parent_session_id
        self.platform = platform
        self.user_id = user_id
        self.user_name = user_name
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.chat_type = chat_type
        self.thread_id = thread_id
        self.gateway_session_key = gateway_session_key
        self.pass_session_id = pass_session_id
        self.persist_session = persist_session

        # Memory / context flags
        self.skip_context_files = skip_context_files
        self.skip_memory = skip_memory

        # Persistence
        self.session_db = session_db
        self.credential_pool = credential_pool

        # Checkpointing
        self.checkpoints_enabled = checkpoints_enabled
        self.checkpoint_max_snapshots = checkpoint_max_snapshots

        # Callbacks — hold references so the fleet can validate wiring.
        self._callbacks: Dict[str, Any] = {
            "tool_progress": tool_progress_callback,
            "tool_start": tool_start_callback,
            "tool_complete": tool_complete_callback,
            "thinking": thinking_callback,
            "reasoning": reasoning_callback,
            "clarify": clarify_callback,
            "step": step_callback,
            "stream_delta": stream_delta_callback,
            "interim_assistant": interim_assistant_callback,
            "tool_gen": tool_gen_callback,
            "status": status_callback,
        }

        logger.debug(
            "AIAgent shell constructed: model=%r provider=%r session=%s",
            self.model, self.provider, self.session_id,
        )

    def run_conversation(
        self,
        message: str,
        *,
        stream_callback: Optional[Any] = None,
    ) -> str:
        """Phase-1 Duo placeholder.

        The real runtime lands in Phase-2 — see design-10 §4 for the
        scoping decision. Calling this during Phase-1 is a programmer
        error and we surface it immediately rather than silently echoing
        back an empty string.
        """
        raise NotImplementedError(
            "Phase-1 Duo: run_conversation is not wired yet. "
            "The fleet orchestrator owns the conversation loop; see design-10."
        )


def run_conversation(agent: AIAgent, message: str, **kwargs: Any) -> str:
    """Module-level convenience wrapper that delegates to the instance."""
    return agent.run_conversation(message, **kwargs)
