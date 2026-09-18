"""Runtime domain (Phase 2): provider-neutral execution, model, scheduling and
orchestration ports.

The core never implements an LLM runtime, never binds to a provider, and never
does autonomous reasoning. It defines the *contracts* (and dataclasses) that
later adapters (custom runtimes, Dify, provider SDKs) implement, plus a
deterministic MockRuntime used to test the kernel end to end.
"""
