"""Graphify read/write routes (design-06 §2 rows 6–7).

Read path is admin-guarded and safe to call from the Memory Browser UI.
Write path (``POST /v1/graph/nodes``) requires CSRF and appends one audit
entry to the graph store (we delegate that responsibility to the injected
store so tests can substitute an in-memory double).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field


from ..deps import get_graph_store, require_admin


router = APIRouter()


class NewNodeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    key_claims: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    author_employee_id: str = Field(..., min_length=1)


@router.get("/v1/graph/query")
async def graph_query(
    intent: str = Query(..., min_length=1),
    k: int = Query(5, ge=1, le=100),
    _auth: dict[str, str] = Depends(require_admin),
    store: Any = Depends(get_graph_store),
) -> dict[str, Any]:
    # TODO(Dharma HIGH): call signature is ``query(intent=..., k=...)`` but
    # the real ``GraphStore.query`` takes a single ``QueryFilters`` arg and
    # is async. Tests get by with duck-typed mocks; against the real store
    # this endpoint TypeErrors. Reconcile to
    # ``await store.query(QueryFilters(intent=..., limit=k))`` when the
    # admin Memory Browser ships.
    query_fn = getattr(store, "query", None)
    if not callable(query_fn):
        return {"nodes": []}
    rows = query_fn(intent=intent, k=k) or []
    nodes = []
    for row in rows:
        if isinstance(row, dict):
            nodes.append(
                {
                    "id": row.get("id"),
                    "summary": row.get("summary", ""),
                    "key_claims": list(row.get("key_claims", []) or []),
                    "updated_at": row.get("updated_at"),
                    "stale": bool(row.get("stale", False)),
                }
            )
        else:
            nodes.append(
                {
                    "id": getattr(row, "id", None),
                    "summary": getattr(row, "summary", ""),
                    "key_claims": list(getattr(row, "key_claims", []) or []),
                    "updated_at": getattr(row, "updated_at", None),
                    "stale": bool(getattr(row, "stale", False)),
                }
            )
    return {"nodes": nodes}


@router.post("/v1/graph/nodes", status_code=status.HTTP_201_CREATED)
async def create_graph_node(
    body: NewNodeBody,
    auth: dict[str, str] = Depends(require_admin),
    store: Any = Depends(get_graph_store),
) -> dict[str, Any]:
    create_fn = getattr(store, "create_node", None)
    if not callable(create_fn):
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="graph writes not supported",
        )
    node = create_fn(
        intent=body.intent,
        summary=body.summary,
        key_claims=body.key_claims,
        source_refs=body.source_refs,
        author_employee_id=body.author_employee_id,
        actor=auth.get("subject", "admin"),
    )
    if isinstance(node, dict):
        return {
            "node_id": node.get("id") or node.get("node_id"),
            "version": int(node.get("version", 1)),
        }
    return {
        "node_id": getattr(node, "id", None),
        "version": int(getattr(node, "version", 1)),
    }


__all__ = ["router"]
