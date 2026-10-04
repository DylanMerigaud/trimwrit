#!/usr/bin/env python3
"""vendor_gates.py: copy the one source of shared gate code into every plugin that ships it.

Claude Code copies a plugin ALONE into its cache, so a plugin cannot import a sibling directory.
gates/gatekit/*.py is copied byte for byte into plugins/<name>/gatekit/ for every plugin
directory, and the shell reader of main-checkout-guard into no-third-party-pr/lib/. Never edit
a copy: edit the source and run this.

    python3 tools/vendor_gates.py           write every copy, remove stale files
    python3 tools/vendor_gates.py --check   change nothing, exit 1 on any drift
"""
import filecmp
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "gates", "gatekit")
PLUGINS = os.path.join(ROOT, "plugins")
EXTRA = [(os.path.join("plugins", "main-checkout-guard", "scripts", "git_hub_guard.py"),
          os.path.join("plugins", "no-third-party-pr", "lib", "git_hub_guard.py"))]


def plugin_dirs():
    if not os.path.isdir(PLUGINS):
        return []
    return sorted(os.path.join(PLUGINS, d) for d in os.listdir(PLUGINS)
                  if os.path.isfile(os.path.join(PLUGINS, d, ".claude-plugin", "plugin.json")))


def source_files():
    return sorted(f for f in os.listdir(SOURCE) if f.endswith(".py"))


def pairs():
    out = []
    for p in plugin_dirs():
        for f in source_files():
            out.append((os.path.join(SOURCE, f), os.path.join(p, "gatekit", f)))
    for src, dst in EXTRA:
        src, dst = os.path.join(ROOT, src), os.path.join(ROOT, dst)
        if os.path.isdir(os.path.dirname(os.path.dirname(dst))) and os.path.isfile(src):
            out.append((src, dst))
    return out


def missing_sources():
    """An EXTRA copy whose destination plugin exists but whose source does not: never dropped
    silently, it is drift."""
    return [os.path.join(ROOT, src) for src, dst in EXTRA
            if os.path.isdir(os.path.dirname(os.path.dirname(os.path.join(ROOT, dst))))
            and not os.path.isfile(os.path.join(ROOT, src))]


def stale():
    keep = set(source_files())
    out = []
    for p in plugin_dirs():
        d = os.path.join(p, "gatekit")
        if os.path.isdir(d):
            out += [os.path.join(d, f) for f in os.listdir(d)
                    if f.endswith(".py") and f not in keep]
    return out


def main(argv):
    check = "--check" in argv
    drift = [dst for src, dst in pairs()
             if not (os.path.isfile(dst) and filecmp.cmp(src, dst, shallow=False))]
    old = stale()
    lost = missing_sources()
    for path in lost:
        print("missing source: " + os.path.relpath(path, ROOT))
    if check:
        for path in drift:
            print("drift: " + os.path.relpath(path, ROOT))
        for path in old:
            print("stale: " + os.path.relpath(path, ROOT))
        return 1 if drift or old or lost else 0
    if lost:
        return 1
    for src, dst in pairs():
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        shutil.copymode(src, dst)
    for path in old:
        os.remove(path)
    print("vendored {} file(s), removed {} stale".format(len(pairs()), len(old)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
