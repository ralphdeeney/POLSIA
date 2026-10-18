# agent_reflection (v1)

Before finishing, check your own run: did you use only authorized tools, stay in
this company's context, and produce evidence for every claim in `output`? If any
answer is no, do not emit the completion contract — emit a failure report
instead.

    {"type": "final", "output": {...}, "summary": "..."}
    or, when evidence is missing:
    {"type": "final", "failed": true, "summary": "why evidence is missing"}
