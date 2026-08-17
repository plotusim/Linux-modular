"""Core APIs for the Linux function-level modularization pipeline."""

from .model import (
    CURRENT_SCHEMA_VERSION,
    EdgeKind,
    EntityKind,
    ExecutionContext,
    FunctionIdentity,
    GlobalIdentity,
    Linkage,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNode,
)

__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "EdgeKind",
    "EntityKind",
    "ExecutionContext",
    "FunctionIdentity",
    "GlobalIdentity",
    "Linkage",
    "ReferenceEdge",
    "ReferenceGraph",
    "ReferenceNode",
]
