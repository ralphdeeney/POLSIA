# agent_result_verification (v1)

A run finishes only by emitting the completion contract below. The completion
contract is a JSON object; free text does not satisfy it.

    {"type": "final", "output": {...}, "summary": "..."}

`output` must contain the concrete evidence produced by the tools you called
(such as file contents you read or the deterministic echo you issued). Do not
claim completion from memory of what you intended to do.
