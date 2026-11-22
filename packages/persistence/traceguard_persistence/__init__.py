"""TraceGuard 持久化层。"""

from __future__ import annotations

from .catalog import DEFAULT_WORKSPACE, CatalogService
from .db import Database, get_database, set_database
from .stores import (
    ArtifactStore,
    DocRecord,
    DocStore,
    PersistentConsumerInbox,
    PersistentEventStore,
    PersistentEvidenceManager,
)

__all__ = [
    "DEFAULT_WORKSPACE",
    "ArtifactStore",
    "CatalogService",
    "Database",
    "DocRecord",
    "DocStore",
    "PersistentConsumerInbox",
    "PersistentEventStore",
    "PersistentEvidenceManager",
    "get_database",
    "set_database",
]
