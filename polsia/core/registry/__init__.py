"""Registry domain (Phase 2): Agent, Skill, Tool, Model registries.

Scoping (approved decision C2):
- Agent is company-scoped configuration (type is a free-form string; the core
  never hard-codes agent types like "ceo" or "engineer").
- Skill, Tool and Model are platform-global, versioned, unique by
  ``name@version``. Global availability is separate from authorization:
  a registered capability does not imply any company or agent may use it.
"""
