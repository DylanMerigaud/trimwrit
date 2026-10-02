"""Which model a `claude -p` call names, and which model really answered it.

An alias ("sonnet", "opus") resolves to whatever Claude Code ships that week, so two replays of
the same case a month apart can measure two different models while the history line says they
measured the same thing. Every `claude -p` trimwrit makes therefore names a FULL id from
`KNOWN_MODELS`, checked by `require_full_id` before any subprocess starts, and every result
records the `modelUsage` Claude Code reports, which is the record of who actually answered.

The table is the source: there is no environment variable or config file that widens it. A new
model is a one line diff here.
"""

KNOWN_MODELS = (
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-4-5-20251001",
)

# The case runs measure the harness under test, so they run on the strongest working model.
DEFAULT_CASE_MODEL = "claude-opus-5-5"
# A judge answers PASS or FAIL on one output against one rule; it does not need the top model.
DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"


def require_full_id(model):
    """Return `model` when it is a known full id, raise ValueError otherwise."""
    if model in KNOWN_MODELS:
        return model
    raise ValueError("{!r} is not a full model id. trimwrit names a full id on every claude -p "
                     "call, never an alias; use one of: {}".format(model, ", ".join(KNOWN_MODELS)))


def model_usage_of(event):
    """The `modelUsage` dict of a result event or a json envelope, {} when absent or malformed."""
    usage = (event or {}).get("modelUsage") if isinstance(event, dict) else None
    return dict(usage) if isinstance(usage, dict) else {}
