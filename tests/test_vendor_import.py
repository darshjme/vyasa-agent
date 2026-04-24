"""Smoke tests for the vendored Phase-1 Duo runtime.

Asserts the public surface of ``vyasa_internals`` is importable and that
``AIAgent.__init__`` still accepts the kwargs the fleet wires against
(design-02 §3: identity, toolsets, persistence, provider routing).

Kept intentionally tiny — the runtime itself is a shell in this alpha;
these tests are the contract that pins the constructor signature so the
shell doesn't drift from the wiring while we are still trimming the
real runtime in for Phase-2.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR_ROOT = REPO_ROOT / "vendor"
if VENDOR_ROOT.is_dir() and str(VENDOR_ROOT) not in sys.path:
    sys.path.insert(0, str(VENDOR_ROOT))


def test_package_imports_cleanly() -> None:
    """``import vyasa_internals`` must succeed without side effects blowing up."""
    import vyasa_internals

    assert vyasa_internals.__name__ == "vyasa_internals"


def test_public_surface_is_exposed() -> None:
    """The symbols the fleet relies on must be re-exported at package root."""
    import vyasa_internals

    required = {
        "AIAgent",
        "IterationBudget",
        "run_conversation",
        "get_tool_definitions",
        "set_session_context",
        "clear_session_context",
        "SessionDB",
        "get_vyasa_home",
        "now",
    }
    missing = required - set(dir(vyasa_internals))
    assert not missing, f"missing from vyasa_internals public surface: {sorted(missing)}"


@pytest.mark.parametrize(
    "kwarg",
    [
        "ephemeral_system_prompt",
        "enabled_toolsets",
        "session_db",
        "model",
        "provider",
    ],
)
def test_ai_agent_constructor_kwarg(kwarg: str) -> None:
    """``AIAgent.__init__`` must accept the design-02 kwargs verbatim."""
    from vyasa_internals import AIAgent

    sig = inspect.signature(AIAgent.__init__)
    assert kwarg in sig.parameters, (
        f"AIAgent.__init__ is missing the '{kwarg}' keyword argument. "
        "This kwarg is part of the fleet contract — see design-02."
    )


def test_ai_agent_constructs_with_design02_kwargs() -> None:
    """Builds an AIAgent with the full design-02 kwarg set; must not raise."""
    from vyasa_internals import AIAgent, SessionDB

    agent = AIAgent(
        model="partner-duo-v1",
        provider="fleet-local",
        ephemeral_system_prompt="You are Vyasa.",
        enabled_toolsets=["core"],
        session_db=SessionDB(),
    )
    assert agent.model == "partner-duo-v1"
    assert agent.provider == "fleet-local"
    assert agent.ephemeral_system_prompt == "You are Vyasa."
    assert agent.enabled_toolsets == ["core"]
    assert agent.session_db is not None


def test_run_conversation_is_phase1_placeholder() -> None:
    """Phase-1 Duo: the conversation loop is not wired, so it must raise loudly."""
    from vyasa_internals import AIAgent

    agent = AIAgent(model="m", provider="p")
    with pytest.raises(NotImplementedError):
        agent.run_conversation("hello")
