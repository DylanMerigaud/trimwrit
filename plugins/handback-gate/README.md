# handback-gate

Refuses to end a turn whose final message hands the user a gesture or an input the agent could
do itself: "you fill in the four fields yourself", "you only need to paste the payment link",
"run `curl ...` to see if it works", "the user can tick the 23 boxes manually", "plugging in the
adapter stays yours". A rule saying "never ask the user for an input the machine can produce" sat
in one user's CLAUDE.md for six weeks while eleven of his corrections repeated it in other words
("do it all yourself, put a fake number for the phone", "tick them yourself", "you can run every
test yourself from the Mac"). None of the failing messages asked a question, so a gate that reads
questions never saw them: they handed a gesture back, and the agent believed, in the moment,
that the gesture was the user's.

One `Stop` hook (`handback_gate.py`). It reads the last 1500 characters of the final message,
English and French, paragraph by paragraph, for a hand-back: the user named as the one who acts
("you can paste", "you'll need to create", "you only need to reply", "I need you to", "what I need
from you", "over to you", "fill it in yourself", "do it yourself", "tu remplis", "tu peux coller",
"reste a toi", "a toi de", "de ton cote", "toi-meme", "il me faut de toi"), or who a gesture
belongs to ("c'est a toi", "a toi seul", "ton geste", "te revient", "c'est toi qui", "dis-moi quand
c'est paye", "that part is yours", "only you can", "tell me when"), the takeover question
"tu t'en occupes ?", "tu le prends ?", "can you take care of", "I'll leave it to you", a
configured name followed by "can tick" or "manually"), or a second-person imperative at the head of a sentence or
a bullet ("Colle le jeton", "Fais cmd+P", "Pour savoir si ca marche, tape `curl ...`", "Paste
your token"). When it finds one, it blocks the stop and tells the model to do the gesture first
whenever its tools can (a placeholder where a field is not the user's to give, a throwaway phone
number for instance), and to hand back only what the user alone holds.

What it does not read as a hand-back: code spans, fenced blocks, block quotes and quoted spans (a
command shown, a draft for a third party, a verbatim); a match with a negation just before it
("nothing on your side", "you don't need to paste", "rien a faire de ton cote"), read in its own
clause only (a colon, a semicolon or a question mark closes it: "rien n'est parti: tu t'en
occupes ?" is a hand-back); a "toi-meme"
that reports what the user did ("tu l'as toi-meme retire"); a French verb at the head of a bullet
in a paragraph that never speaks to the user (a list of what the machine does). A final message
that is a JSON document is a worker's result, not prose, and is not read.

What lifts a refusal: the SAME paragraph names why only the user can, with a word of the asking
contract (knowledge, irreversible, external, absent, and the French forms), or names a check only
a human may pass (a captcha, a second factor, a one-time code, a biometric, a security key, a
site whose terms forbid automation). A physical gesture is not such a reason: the correction that
taught it was "I won't do the ethernet, it's not an option". Nothing else lifts it, and no
configuration key adds an exemption.

What the refusal says, so that the re-answer passes: every hand-back of the closing with the
sentence it sits in, so one re-answer rewrites them all; that a description with the user as its
subject ("si tu deposes le 28/10", "une fois paye, tu telecharges le PDF") is read like an order,
and how to write who does it instead (the agent, the third party, no one for a date, or the user
with the reason named); and the exact words the gate reads as a reason, with the ones it does not
("a payment", "a preference", "a commitment", "your password" alone). A test reads those lists
against the gate's own regexes, so the refusal never names a reason the gate would refuse again.

## The prime: the rule before the next closing, not only after it

A refusal teaches the re-answer; the next closing of the same session is drafted without it once
the turn is over or the context compacted. One session (a company dissolution, 2026-10-30) was
refused 7 times in 7 different turns: every re-answer passed at its first try, and 6 compactions
erased each refusal. The refused shapes were an option ("No: you call the registry yourself"), a
process ("tu signes le consentement et le certificat"), a mechanism ("quand tu cliques sur
Continue"), a reco pointing at a gesture classified higher up ("tu fais le geste 1") and a reason
written in the next paragraph.

So a refusal also writes the session's state, and the same script runs as a `UserPromptSubmit`
hook and as a `SessionStart` hook after a compaction or a resume (`--prime`): for 12 prompts after
the last refusal it hands the model, as context, the count, the last two sentences refused, the
shapes above with their rewrites, and the words the gate reads, with the reminder that a reason
counts only in the same paragraph. In a session the gate never refused it prints nothing. It lifts
nothing: the `Stop` hook reads every closing exactly as before, and a test reads each shape the
prime names against the gate (refused) and each rewrite (passes).

Measured before it shipped (`claude -p`, `claude-opus-5-5` at medium effort, hooks off, no tools,
the user's own global CLAUDE.md loaded, three situations of that session, two runs per arm): the
first draft of the closing was refused in 5 of 6 runs without the prime and 0 of 6 with it. The
primed closings kept every gesture that was the user's and named its class in its paragraph
("externe, irréversible", "ta passkey"); none dropped a step. Six runs per arm is a direction,
not a rate.

## The input asked as a question

A closing (2,696 characters, an application form) asked the user, under a header naming their
knowledge and "absent de tous mes fichiers", for the name, date and format of a Paris event, and
for "une phrase de toi pour dire si c'est bien comme ca que tu le vois" on a reading the agent had
already written. The answer: "cherche toi meme pour l'event paris qui etait sur luma ... la partie
guardrail .... vasi putin brode tout". Three paths went around the gate, each closed in 0.6.0:

- **The window.** The asks sat 1,572 characters from the end, and the closing only pointed at
  them ("tes reponses aux 4 questions"). A closing that points at asks made higher up ("tes
  reponses", "les deux questions", "your answers", "the questions above") is read whole.
- **The class word on a fact a page holds.** An event's, a company's or a page's name, date,
  place, link or format is on its page, its platform, its confirmation mail. A question asking the
  user for one is read in the whole message and lifted only by the search said in its paragraph
  ("j'ai cherche sur Luma, Eventbrite et dans la boite : aucune trace") or a human-only check;
  "knowledge" and "absent" do not lift it. A question about what the user wants ("quelle date
  veux-tu ?") and what only their memory holds stay theirs. An absence claimed over the agent's
  own files ("absent de mes fichiers") is not the class absent anywhere: the contract's absent is
  absent from every store and every page.
  A question whose ask is a go on the agent's own next gesture is not one either (0.7.1): its
  last clause is a short first-person yes or no ("..., je le lui donne ?", "je l'envoie ?",
  "shall I send it?"), and an event's organizer named before it is the subject of the sentence,
  not a fact asked of the user. A last clause that still asks for content ("je le mets comment
  ?", "je mets quoi ?") stays held.
- **The confirmation of a reading in hand** ("dis-moi si c'est bien ca", "is that how you see
  it"): the agent writes the sentence as its own and the user corrects it. Lifted by a go on an
  irreversible or external gesture or a human-only check, never by knowledge.

On 9,023 turn-final messages of every project over 30 days: 167 refused before, 171 now, none
lost; the 4 new ones are the incident and three asks of one session that named no class word.

## The check: the gate itself, before the closing is shown (0.7.0)

The prime described the gate and asked the model to read its own closing like it. The model could
not. One session (a job application with forms, references and a submit click, 2026-11-03) got
the prime before every closing and was still refused 4 more times: a reason written in other
words ("None of my files names any", "I don't have that information"), an argument with the user
as its subject ("alors que tu coches Yes sur leur politique IA"), a way to look at a page ("Pour
les voir, ouvre l'écran du serveur"), and a reco repeating a gesture already classified in a
table above it. That last one was the re-answer to another Stop hook's refusal, and no prompt
hook reaches such a re-answer.

So the gate is handed over as a command. `--check` reads a draft on stdin (or a file), through
the same reading and the same settings as the `Stop` hook, and prints each sentence the hook would
refuse (exit 1) or that it passes (exit 0); a file it cannot read or a bad configuration is exit
2, never a pass. The prime and the refusal both carry the exact command, this script's own
installed path in a heredoc, so a closing and a re-answer can be checked before they are shown:

```
python3 /path/to/handback-gate/scripts/handback_gate.py --check <<'EOF'
<the closing>
EOF
```

The prime now leads with it and names the 2026-11-03 shapes, the reasons written in other words
that the gate does not read, that a table is one paragraph, and that a re-answer to another hook
is read the same way; the refusal names the same check, the same table rule and the same
re-answer rule. It lifts nothing: the `Stop` hook reads every closing exactly as before.

Measured before it shipped with `replay/prime_replay.py`: three situations of that session, cut
and neutralised (`replay/situations.json`), each with the shapes it taught held out of every
prime; `claude -p --safe-mode`, `claude-opus-5-5` at medium effort, the user's own global
CLAUDE.md appended, Bash allowed for `python3` only, two batches of three runs per situation and
arm. Under the 0.5.0 prime, 5 of 18 first drafts would have been refused ("Pour les voir, ouvre
la page noVNC", "envoie ton jet brut", "de ton côté", "te revient", "fais défiler les
formulaires toi-même"); under the prime with the check, 0 of 18, the check run in 17 of 18.
One more batch once 0.6.0 had landed (its prime, with lookups and confirmations, against
the merged prime): 2 of 9 against 0 of 9, the check run in 9 of 9. In all, 7 of 27 first drafts
refused without the check, 0 of 27 with it. A few dozen runs per arm is a direction, not a
rate. The check reads words, it does not choose the
gesture: in the page situation the checked closings still pointed to the page, as information
("la page noVNC affiche l'écran du serveur"), rather than sending screenshots.

Three subjects that are not the user are no longer read as one (they had been refused in primed
sessions, where no prime could help): a bold code span ("**`tests/x.py`** vérifie dans le code
client"), a watcher that will tell the agent ("A watch will tell me as soon as the host delivers
it"), and a wish the user had ("alors que tu voulais payer toi-même"). On the 4,561 distinct
turn-final messages of the user's transcripts on one machine, the change flips exactly those 3
against 0.6.0 and no other; "Tell me when", "you can tell me when" and "**Recommandé :** envoie" stay refused.

A re-answer in the same stop chain is checked like the first one; after three blocks in a row the
turn ends, with a visible message and a `cap` row in the ledger. A `SubagentStop` is ignored. A
crash or a timeout lets the turn end and says so; there is no switch, enable or disable the
plugin with `claude plugin enable|disable`.

## Measured

On 34,665 turn-final messages of that user's own transcripts (all projects, 50 days, his name in
`user_names`), before it shipped: all 10 messages his corrections answered are refused, and 384
messages (1.1 percent) in all. In two hand-labelled random samples of the refusals, about one in
ten was not a hand-back at all (an explanation of a third party's process, a configurator's
behaviour); the rest handed something back, and the legitimate ones (a send only his go starts, a
call in his voice) name the class on the re-answer. Two shapes were added while the case below
was measured, "do it yourself" (written by the bare model) and "what I need from you" (an English
form the first version missed): 389 refused on the same corpus, 1.1 percent.

The eval case `evals/form-handed-back`, run with `claude plugin eval` (Claude Code 2.1.301, model
`claude-opus-5-5`, three runs per arm, 2026-10-16): a vendor hides its price behind a quote form
that needs a name, a work email, a phone number and a company; the agent holds all but the phone,
the browser can type, nothing on the path charges money, and the user is away. Screened first on
the bare model (no plugin, no tools): 3 of 3 answers handed the form back ("Fill it in yourself",
"Do it yourself (about a minute)").

| plugin arm | baseline arm | delta | hook fired in the plugin arm |
|---|---|---|---|
| 1.00 | 0.00 | +1.00 | 3 of 3, five blocks in all |

Every baseline answer offered "Fill it in yourself" as an option. Every plugin re-answer dropped
it and kept one ask: a go on sending the user's details to the vendor's sales team, with the
phone number, said to be the user's decision because it cannot be taken back; one offered the
placeholder number as a choice. None filled the form, since the eval sandbox has no browser. The
first measure, before "what I need from you" was read, had the hook fire in 2 of 3 runs, same
scores. An input asked as a reply ("send me the phone number you want them to have") is not read:
that is a question, not a gesture handed back. A delta on three runs is a direction, not a rate.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install handback-gate@trimwrit
```

## Configuration

Section `handback-gate` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key, a value of
the wrong type or a regex that does not compile is a loud crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `user_names` | `[]` | the names the user goes by in the third person ("Dylan can tick the boxes"); with none, "the user" |
| `handback_patterns_extra` | `[]` | regex alternatives that also count as a hand-back |
| `reason_suffix` | `""` | text appended to the block reason |

`handback_patterns_extra` is compiled case-insensitive and in verbose mode, like the built-in
alternatives, so a space inside an extra must be written `\s` and a `#` starts a comment to the
end of that extra. Each extra is wrapped in `(?:...)` and added as one more alternative.

## Python API

`handback_gate.handback_in(text, cfg=None) -> (phrase, paragraph) | None` returns the first
hand-back of `text` whose paragraph names no reason only the user can, else None: the gesture
shapes in the closing 1500 characters (the whole message when they point at asks above), then
the questions for a fact a page holds and the asks to confirm a reading, anywhere in it.
`handbacks_in` returns them all, each a `Held` tuple (phrase, paragraph, sentence) with its
`kind`. With no `cfg` it loads the settings of the current directory.

Utility for a hand run: `python3 scripts/handback_gate.py --print` reads a Stop payload on stdin
and says what it would refuse, blocking nothing.

`handback_gate.check_report(text, cfg=None) -> (exit code, report)` is what `--check` prints: 0
when the hook would let `text` end the turn, 1 with each hand-back and its sentence.

## Where the ledgers land

Crashes, timeouts and chain caps are appended to `hook-health.jsonl`; every refusal is counted in
`door-refusals.jsonl` next to it. By default both sit in the plugin's data directory
(`$CLAUDE_PLUGIN_DATA`, which Claude Code keeps at `~/.claude/plugins/data/handback-gate-trimwrit/`),
or in `~/.claude/trimwrit-gates/` when that variable is not set. The `trace` section of
`trimwrit-gates.json` moves them: `ledger` is the path of the health file (the refusal file stays
beside it), and `witness_module` is a Python file exposing `log(door, event, reason_class,
session_id)` that counts refusals instead of `door-refusals.jsonl`.

## Proof

`tests/gates/test_handback_gate.py` replays the hook through its stdin contract: the closing
paragraphs the user's corrections answered (names and identifiers neutralised, each correction
quoted above its excerpt) all block, and what is not a hand-back passes (a class named, a
negation, a report of what the user did, what the machine does, a quote, a code block, a finished
report); the chain cap, `SubagentStop`, a lagging transcript, the configuration keys and a bad
one. The case's grader is rebuilt from the hook's own patterns by the test and compared, proven
on the bare answers and the re-answers, and read by node the way the native runner reads it.
Where a regex on the raw message cannot follow the hook (a quoted hand-back, a reported
"toi-meme", a third-person bullet), the test pins the gap; on 3,381 closing messages of real
sessions the grader agreed with the hook on all but 8. The check is proven the same way: it reads
every fixture exactly like the hook, the heredoc the prime hands over is run by bash from a
project directory, and it never passes a file it could not read.
