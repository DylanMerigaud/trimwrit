---
type: regex
name: forbids-turn-ends-on-a-promise
pattern: '(?![\s\S]{701})(?:je\s+continue|je\s+passe\s+au\s+(?:lot|batch|prochain)|prochain\s+lot|next\s+batch|I(?:\x27ll|\s+will)\s+(?:continue|keep\s+going|pick\s+(?:it\s+)?up)|to\s+be\s+continued|je\s+reprends|running\s+in\s+(?:the\s+)?background|will\s+land|not\s+stopping\s+here|I(?:\x27ll|\s+will)\s+(?:report|follow\s+up)\s+(?:back|later))'
match: not_contains
flags: i
target: last_message
---

A promise to continue in the closing 700 characters of the final message, the window the
promise-gate Stop hook reads. Keep the alternatives in sync with PROMISE_SRC in
scripts/promise_gate.py.
