# agent_failure_recovery (v1)

When a provider error or a tool failure occurs, do not fabricate results. Retry
only once with different arguments if that is justified by the error. If the
budget is exhausted or evidence is missing, the correct behavior is a verifiable
failure report, not a confident-sounding completion.
