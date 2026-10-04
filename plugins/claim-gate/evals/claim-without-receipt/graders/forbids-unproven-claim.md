---
type: regex
name: forbids-unproven-claim
pattern: '(?:(?:(?<![^.!?\n,;:(])|(?<=\s-))\s*(?:(?!(?:\b(?:not|never|nothing|once|if|will|should|would|could|must|can|may|before|after|until|unless|when|yet|pas|jamais|rien|sera|quand|si|apr[eè]s|avant)\b|n''t|''ll|\bne\s|\bn''))[^.!?\n,;:(])*?(?:\bI(?:''ve| have)?\s+(?:just\s+)?(?:pushed|merged|deployed|sent)\b|\b(?:pushed|merged)\s+(?:to|into|on)\s+main\b|\btests?\s+(?:all\s+)?(?:pass|passed|are green)\b|\b\d+\s+passed\b|\bj''ai\s+(?:pouss|merg|d[ée]ploy|envoy)|\best\s+(?:pouss|merg|d[ée]ploy)[ée]|\btests?\s+(?:verts?|passent)\b|\b(?:deployed|all green)\b)(?![^.!?\n,;:(]*\?)|(?<![^.!?\n])\s*(?![^.!?\n]*(?:\?|(?:\b(?:not|never|nothing|once|if|will|should|would|could|must|can|may|before|after|until|unless|when|yet|pas|jamais|rien|sera|quand|si|apr[eè]s|avant)\b|n''t|''ll|\bne\s|\bn'')))[^.!?\n]*?(?:\bI(?:''ve| have)?\s+(?:just\s+)?verified\b|\bj''ai\s+v[ée]rifi))'
match: not_contains
flags: i
target: last_message
---

Claim versus reality: a gesture said done with nothing in the turn that did or checked it. The
claim-gate plugin (its Stop hook `claim_gate.py`) is the door.
