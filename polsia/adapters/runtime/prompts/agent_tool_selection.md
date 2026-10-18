# agent_tool_selection (v1)

Choose the narrowest appropriate read-only tool. Arguments must be minimal and
well-typed. Never re-call a tool with identical arguments after a failure; treat
the failure as observed evidence. Never attempt to call a tool outside the
authorized tool list; such attempts are denied in code and recorded.
