---
type: regex
name: forbids-id-shown-bare
pattern: '(?:^|\n)(?:[^(\n`]|\([^()\n]*\)|`[^`\n]*`)*?(?:`(?:(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|(?!(?:covid|Covid|COVID|sha|Sha|SHA|iso|Iso|ISO|rfc|Rfc|RFC|utf|Utf|UTF|gpt|Gpt|GPT|ipv|Ipv|IPV|cve|Cve|CVE|win|Win|WIN|windows|Windows|WINDOWS|ubuntu|Ubuntu|UBUNTU|python|Python|PYTHON|node|Node|NODE|ecma|Ecma|ECMA|es|Es|ES|top|Top|TOP|fortune|Fortune|FORTUNE)-|(?:pre|Pre|PRE|post|Post|POST|early|Early|EARLY|mid|Mid|MID|late|Late|LATE|end|End|END|start|Start|START|fy|Fy|FY|cy|Cy|CY|dv|Dv|DV|h1|H1|h2|H2|q1|Q1|q2|Q2|q3|Q3|q4|Q4|spring|Spring|SPRING|summer|Summer|SUMMER|fall|Fall|FALL|autumn|Autumn|AUTUMN|winter|Winter|WINTER)-(?:19|20)\d\d(?![\w-]))[A-Za-z][A-Za-z_]+-\d{2,})(?:\.\.\.?(?:(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|(?!(?:covid|Covid|COVID|sha|Sha|SHA|iso|Iso|ISO|rfc|Rfc|RFC|utf|Utf|UTF|gpt|Gpt|GPT|ipv|Ipv|IPV|cve|Cve|CVE|win|Win|WIN|windows|Windows|WINDOWS|ubuntu|Ubuntu|UBUNTU|python|Python|PYTHON|node|Node|NODE|ecma|Ecma|ECMA|es|Es|ES|top|Top|TOP|fortune|Fortune|FORTUNE)-|(?:pre|Pre|PRE|post|Post|POST|early|Early|EARLY|mid|Mid|MID|late|Late|LATE|end|End|END|start|Start|START|fy|Fy|FY|cy|Cy|CY|dv|Dv|DV|h1|H1|h2|H2|q1|Q1|q2|Q2|q3|Q3|q4|Q4|spring|Spring|SPRING|summer|Summer|SUMMER|fall|Fall|FALL|autumn|Autumn|AUTUMN|winter|Winter|WINTER)-(?:19|20)\d\d(?![\w-]))[A-Za-z][A-Za-z_]+-\d{2,}))?`|(?<![\w#/@`-])(?<![^.]\.)(?:(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|(?!(?:covid|Covid|COVID|sha|Sha|SHA|iso|Iso|ISO|rfc|Rfc|RFC|utf|Utf|UTF|gpt|Gpt|GPT|ipv|Ipv|IPV|cve|Cve|CVE|win|Win|WIN|windows|Windows|WINDOWS|ubuntu|Ubuntu|UBUNTU|python|Python|PYTHON|node|Node|NODE|ecma|Ecma|ECMA|es|Es|ES|top|Top|TOP|fortune|Fortune|FORTUNE)-|(?:pre|Pre|PRE|post|Post|POST|early|Early|EARLY|mid|Mid|MID|late|Late|LATE|end|End|END|start|Start|START|fy|Fy|FY|cy|Cy|CY|dv|Dv|DV|h1|H1|h2|H2|q1|Q1|q2|Q2|q3|Q3|q4|Q4|spring|Spring|SPRING|summer|Summer|SUMMER|fall|Fall|FALL|autumn|Autumn|AUTUMN|winter|Winter|WINTER)-(?:19|20)\d\d(?![\w-]))[A-Za-z][A-Za-z_]+-\d{2,}))(?![\w/-]|\.\w)'
match: not_contains
target: last_message
---

An id of the hook's default families (a git sha, a UUID, a ticket-like key such as INT-0412),
bare or in a one-token code span (a commit range a..b included), that no closed parenthesis
holds on its line: what the bare-id-gate Stop hook refuses. The pattern is built from
ID_FAMILIES, LEFT and RIGHT in scripts/bare_id_gate.py, and a test rebuilds it and compares. A
code span holding a space (a command) is skipped whole, as the hook drops it. Known gaps: the
hook also drops fences, quoted spans and block quotes, reads a JSON result as no prose, spares a
label the message numbers from 1, wants a word of prose before the parenthesis, and names only
the first of several ids in one parenthesis; a regex on the raw message cannot.
