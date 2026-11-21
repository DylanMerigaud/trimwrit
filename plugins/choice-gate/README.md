# choice-gate

A choice the user stated in a session is applied, and stays applied after a compaction.

A long session builds something over dozens of prompts (a shopping list, a polish pass on a
game, a day plan) and goes through several compactions. The user states a choice ("je pense
prendre les assortiment de noix lidl", "Remove champomy", "Riesling et Pinot Noir"), the next
summary drops it, and a later version comes back with it undone. Then he has to say it again:
"on a encore bcp de noix de cagout alors quej ai dit un melange de tout?", "on prefera aldi pour
les bouteilles que carrefour. riselsing + pino noir i said". Eleven corrections like these in
two projects between 2026-09-26 and 2026-11-20 (lesson `s:session-feedback-dropped-not-applied`).
A sentence in a CLAUDE.md cannot hold here: the words the model needs are gone from its context.

Three hooks, one script (`scripts/choice_gate.py`):

- `UserPromptSubmit` (`--prompt`): every clause of a typed prompt goes to a per-session ledger
  under the plugin's data directory (`choices/<session hash>.jsonl`), flagged when it states a
  choice: a preference, an exclusion, a pick, a must, a repeat. Harness text (a hook's feedback,
  a skill body, a task notification, a compaction summary) is never recorded. A prompt that
  repeats an earlier choice ("i said", "j'ai dit", "on avait dit", "like i told you") gets the
  whole ledger back as context, and arms the repeat check below for this turn only.
- `SessionStart` after a compaction or a resume (`--session`): the choices are handed back
  verbatim, oldest first, so the summary is never the only memory of what the user chose.
- `Stop`: the final message is refused when
  - a line that lists (a bullet, a table row, a quantity or a price) keeps something the user
    excluded in the session ("Remove X", "pas de X", "sans X", "X => non", "X plutot que Y",
    "on prefere A pour Z que Y" on a line naming Z), with no removal word on that line ("retire",
    "sans", "plus de", "instead"). A later prompt naming the item again outside an exclusion
    takes the exclusion back; a question ("il y a pas de blinis?") rules nothing.
  - the user just repeated a choice and the answer names under half of the words of what he
    repeated (typos tolerated: "riselsing" finds "Riesling", "pino" finds "Pinot").

  The reason quotes his words. After three blocks in a row the turn ends (gatekit chain cap). A
  `SubagentStop` is ignored. A crash or a timeout lets the turn end and says so; there is no
  switch, enable or disable the plugin with `claude plugin enable|disable`.

Settings (`trimwrit-gates.json`, section `choice-gate`): `reason_suffix`, appended to every
refusal.

Tests: `tests/gates/test_choice_gate.py` replays the lesson's corrections verbatim: each is
recorded and handed back after a compaction, and the answer that drops it again is refused.
Eval case: `evals/stated-choice-dropped`.
