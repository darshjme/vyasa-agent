"""Gateway transport types.

Pydantic models that cross the channel-adapter boundary. These are deliberately
thin: just enough to let a router resolve an employee, a dispatcher enqueue the
turn, and an adapter render the reply back to the user's chat surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Platform = Literal["telegram", "whatsapp", "slack", "webhook", "console"]


class Attachment(BaseModel):
    """A single inbound binary payload (image, voice clip, document, ...)."""

    kind: str = Field(..., description="image|audio|video|document|sticker|other")
    url: str | None = Field(None, description="Adapter-resolved URL (local file or remote).")
    mime: str | None = None
    size_bytes: int | None = None
    caption: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class InboundMessage(BaseModel):
    """Normalised inbound message, shape-independent of the adapter."""

    platform: Platform
    platform_user_id: str = Field(..., description="Stable per-platform sender id.")
    platform_chat_id: str = Field(..., description="Chat/thread id to reply into.")
    text: str = Field(default="", description="UTF-8 text, may be empty for media-only.")
    attachments: list[Attachment] = Field(default_factory=list)
    trace_id: str = Field(..., description="Gateway-assigned correlation id, propagates to dispatch.")
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reply_to_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def binding_key(self) -> tuple[str, str]:
        """Sticky-binding key used by the router's short-lived affinity store."""
        return (self.platform, self.platform_user_id)


class ReplyMarkup(BaseModel):
    """Minimal reply-markup abstraction (buttons, quick replies) — adapter maps it."""

    inline_buttons: list[list[dict[str, str]]] = Field(default_factory=list)
    quick_replies: list[str] = Field(default_factory=list)


class OutboundMessage(BaseModel):
    """Outbound reply produced by an employee, rendered by the adapter."""

    target_platform: Platform
    target_chat_id: str
    text: str = ""
    attachments: list[Attachment] = Field(default_factory=list)
    reply_markup: ReplyMarkup | None = None
    trace_id: str
    reply_to_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class HandoffRequest(BaseModel):
    """Employee-to-employee handoff (see design-06 §4)."""

    from_employee_id: str
    to_employee_id: str = Field(..., alias="to")
    intent: str
    context_node_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_output: str | None = None
    confidence_threshold: float = 0.80
    deadline_ms: int = 30_000
    idempotency_key: str | None = None

    model_config = ConfigDict(populate_by_name=True)
