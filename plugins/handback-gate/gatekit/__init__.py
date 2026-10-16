"""gatekit: what every trimwrit gate shares (configuration, chain cap, crash report, witness).

The one source lives in gates/gatekit/ of the trimwrit repository; tools/vendor_gates.py copies
it into every plugin, because Claude Code copies a plugin alone into its cache and a plugin
cannot import a sibling directory. Never edit a vendored copy.
"""
