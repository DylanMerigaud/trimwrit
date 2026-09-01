"""A small YAML-front-matter reader, because the eval case format is YAML and PyYAML is a
dependency this tool refuses to have.

It supports exactly what an eval case file needs: scalars, quoted strings, integers, booleans,
inline lists, block lists, and block scalars. Anything else raises. Failing loudly on a
construct it does not understand is the only safe behaviour here: a front matter key silently
parsed as the wrong type would turn into a grader that never fires, and a grader that never
fires is an instrument that always says PASS.
"""


class FrontmatterError(Exception):
    pass


_TRUE = ("true", "yes", "on", "1")
_FALSE = ("false", "no", "off", "0")


def _scalar(raw):
    v = raw.strip()
    if not v:
        return ""
    if v[0] == "'" and len(v) > 1 and v[-1] == "'":
        # A doubled '' is how a single-quoted YAML scalar escapes a literal quote, and it is
        # the only escape `_quote` below ever writes. Stripping the outer quotes without
        # undoing the doubling silently corrupts any example or pattern that itself contains a
        # quote character: it round-trips as an extra quote character rather than one real one.
        return v[1:-1].replace("''", "'")
    if v[0] == '"' and len(v) > 1 and v[-1] == '"':
        return v[1:-1]
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in _split_inline(inner)] if inner else []
    low = v.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def _split_inline(inner):
    """Split `a, "b, c", d` on commas that are not inside quotes."""
    out, buf, quote = [], [], None
    for ch in inner:
        if quote:
            if ch == quote:
                quote = None
            buf.append(ch)
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf))
    return [x for x in (s.strip() for s in out) if x]


def parse_block(text):
    """Parse a front matter block (without the --- fences) into a dict."""
    data = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[:1] in (" ", "\t"):
            raise FrontmatterError(
                "unexpected indentation at line {}: {!r}. Nested mappings are not supported; "
                "keep eval front matter flat.".format(i + 1, line))
        if ":" not in line:
            raise FrontmatterError("line {} is not `key: value`: {!r}".format(i + 1, line))
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()

        if rest in ("|", "|-", ">", ">-"):
            body, i = _block_scalar(lines, i + 1)
            data[key] = body if rest.startswith("|") else " ".join(body.split())
            continue
        if rest == "":
            items, consumed = _block_list(lines, i + 1)
            if items is not None:
                data[key] = items
                i = consumed
                continue
            data[key] = ""
            i += 1
            continue
        data[key] = _scalar(rest)
        i += 1
    return data


def _block_scalar(lines, start):
    body, i = [], start
    while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
        body.append(lines[i][2:] if lines[i].startswith("  ") else lines[i].strip())
        i += 1
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body), i


def _list_block_scalar(lines, start, dash_col, keep_trailing_newline):
    """Body of a `- |` (keep_trailing_newline) or `- |-` (strip it) list item.

    Same reading rule as `_block_scalar`: blank lines are swallowed into the body and trailing
    blank lines are dropped. The one thing that differs from a bare `key: |` is the stop
    condition. A top level block scalar only ever meets a new top level key, at column 0, but a
    list item's block scalar also has to stop at the NEXT list item, which sits at the same
    column as the dash that opened this one, not at column 0. Using `line[:1] in " \\t"` (what
    `_block_scalar` uses) would swallow that next `- ...` item straight into this one's body.
    """
    content_indent = dash_col + 2
    prefix = " " * content_indent
    body, i = [], start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            body.append("")
            i += 1
            continue
        this_col = len(line) - len(line.lstrip(" "))
        if this_col <= dash_col:
            break
        body.append(line[content_indent:] if line.startswith(prefix) else line.strip())
        i += 1
    while body and not body[-1].strip():
        body.pop()
    text = "\n".join(body)
    # "|" clips to exactly one trailing newline when the body is non empty, "|-" strips it
    # entirely. Multiple trailing blank lines are already gone by the time we get here, same as
    # a bare `key: |`; this repo's parser has never tried to preserve those.
    if keep_trailing_newline and body:
        text += "\n"
    return text, i


def _block_list(lines, start):
    items, i = [], start
    while i < len(lines) and lines[i].strip().startswith("- "):
        line = lines[i]
        dash_col = len(line) - len(line.lstrip(" "))
        rest = line.strip()[2:].strip()
        if rest in ("|", "|-"):
            # An example worth checking a grader against is usually a whole assistant answer,
            # which is multi-line, so a `must_match`/`must_not_match` list has to be able to
            # hold a block scalar as an item, not just a quoted one-liner.
            body, i = _list_block_scalar(lines, i + 1, dash_col,
                                         keep_trailing_newline=(rest == "|"))
            items.append(body)
            continue
        items.append(_scalar(rest))
        i += 1
    return (items, i) if items else (None, start)


def split(text):
    """(frontmatter dict, body string). No fences means no front matter and an all-body file."""
    if not text.startswith("---"):
        return {}, text
    rest = text[3:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end == -1:
        raise FrontmatterError("front matter opened with --- and never closed")
    block = rest[:end]
    body = rest[end + 4:]
    if body.startswith("\n"):
        body = body[1:]
    return parse_block(block), body


def _needs_quote(v):
    """Would this string come back as something other than itself?

    Written after a regex character class went into a file as `pattern: [\\u2014\\u2013]` and came
    back out as a one-element LIST, because a scalar starting with `[` is an inline sequence in
    YAML. The grader still loaded, still reported, and could never fire. That failure mode is
    the exact thing this repo argues against: an instrument that always says PASS.
    """
    if v == "" or v.strip() != v:
        return True
    if v[0] in "[{>|&*!%@`'\"#?,":
        return True
    if "'" in v or '"' in v:
        # Not just a leading quote: `_split_inline` toggles its "inside a quoted segment" state
        # on every quote character it meets, anywhere in the string. An apostrophe in the
        # MIDDLE of an unquoted inline-list item (`it's ok, plain`) opens that state and never
        # closes it, so the comma after it is swallowed and two items come back as one.
        return True
    if ": " in v or v.endswith(":") or " #" in v:
        return True
    if v.lower() in _TRUE + _FALSE:
        return True
    try:
        float(v)
        return True
    except ValueError:
        return False


def _quote(v):
    """SINGLE quotes, deliberately. A double-quoted YAML scalar processes backslash escapes, so
    `"\\u2014"` would be resolved to a literal em-dash by any real YAML parser and the character
    this repo refuses would end up in the file after all. A single-quoted scalar is literal;
    the only escape it has is a doubled quote."""
    return "'{}'".format(v.replace("'", "''"))


def _render_list_block(k, v):
    """A list holding at least one multi-line string, as a dash list with a `- |` block scalar
    for each multi-line item.

    The alternative, keeping the compact `[a, b]` form and escaping the newline inside a
    single-quoted item, is exactly the trap this module exists to avoid: a single-quoted YAML
    scalar has no newline escape, so a real `\\n` written into it would come back as two
    literal characters, backslash and `n`, not a line break. `must_match`/`must_not_match`
    examples are whole assistant answers and need the real thing.
    """
    out = ["{}:".format(k)]
    for x in v:
        s = str(x)
        if isinstance(x, str) and "\n" in s:
            core = s.rstrip("\n")
            indicator = "|" if core != s else "|-"
            out.append("- {}".format(indicator))
            out.extend("  {}".format(line) for line in core.split("\n"))
        else:
            out.append("- {}".format(_quote(s) if _needs_quote(s) else s))
    return out


def render(data):
    """Emit front matter for the keys we write. Deterministic order, because these files land in
    git and a reordering diff is a diff nobody can review."""
    out = []
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, bool):
            out.append("{}: {}".format(k, "true" if v else "false"))
        elif isinstance(v, (list, tuple)):
            if any(isinstance(x, str) and "\n" in x for x in v):
                out.extend(_render_list_block(k, v))
            else:
                out.append("{}: [{}]".format(
                    k, ", ".join(_quote(str(x)) if _needs_quote(str(x)) else str(x) for x in v)))
        elif isinstance(v, str) and "\n" in v:
            out.append("{}: |".format(k))
            out.extend("  " + line for line in v.split("\n"))
        elif isinstance(v, str) and _needs_quote(v):
            out.append("{}: {}".format(k, _quote(v)))
        else:
            out.append("{}: {}".format(k, v))
    return "\n".join(out)


def round_trip(data):
    """Parse back what render() produced. Used by the tests, and by anything that must not ship
    a grader whose pattern changed shape on the way to disk."""
    return parse_block(render(data))
