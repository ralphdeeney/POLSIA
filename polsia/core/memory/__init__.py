"""Memory domain (Phase 2, Slice 5): provider-neutral, company-scoped memory.

MemoryRecord is a business-agnostic record (free-form ``scope``/``type`` and
opaque ``content`` plus arbitrary metadata) — the OS never interprets content
meaning. All storage goes through the :class:`~polsia.core.memory.ports.MemoryPort`
protocol; the simple DB-backed keyword-search adapter ships in this slice and
future providers (e.g. pgvector) implement the port without core changes.
No vector DB, embeddings, RAG, or external memory providers in Phase 2.
"""
