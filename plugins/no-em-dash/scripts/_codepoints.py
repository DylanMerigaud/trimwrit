"""Shared by both no-em-dash hooks: the configured `codepoints`, validated."""
import os
import sys

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_ROOT)
from gatekit import config  # noqa: E402


def refused(settings):
    """The set of refused codepoints. A config that would refuse nothing, or nothing sane, is a
    loud ConfigError (the hook reports it as a crash), never a silent pass."""
    value = settings["codepoints"]
    if not isinstance(value, list) or not value:
        raise config.ConfigError("codepoints must be a non-empty list of integers")
    for cp in value:
        if isinstance(cp, bool) or not isinstance(cp, int) or not 0 <= cp <= 0x10FFFF:
            raise config.ConfigError(
                "codepoints must hold integers in 0..1114111, got {!r}".format(cp))
    return set(value)
