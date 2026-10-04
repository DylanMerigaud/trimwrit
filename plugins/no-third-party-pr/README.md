# no-third-party-pr

A PreToolUse hook on `Bash` that refuses opening a pull request on a GitHub repository you do not
own. A pull request on someone else's repository is a public trace under your name, and an agent
running unattended can open one in a single command.

Refused whenever the target repository's owner is not one of `owners`: `gh pr create` (and
`gh pr new`, and any gh alias that expands to it), `gh api` or `hub api` POSTing to
`repos/<owner>/<repo>/pulls`, a GraphQL `createPullRequest` mutation, `curl` and `wget` POSTing to
the pulls endpoint, `hub pull-request`, inline interpreter code (`python -c`, `node -e`) that opens
one, and a shell script file run with `bash`, `sh` or `source`. The whole command is read as shell
by the reader of `main-checkout-guard` (vendored in `lib/`): `&&`, pipes, subshells, `$(...)`,
`bash -c`, `eval`, `env` prefixes, `xargs`, every `cd` followed.

The target is resolved the way gh does: `-R/--repo`, else `GH_REPO` set by the command itself,
else every git remote of the directory the command runs in (a fork whose `upstream` is a third
party counts as a third-party target). A target that cannot be resolved is refused (fail closed).
Reads (`gh pr list`, `gh pr view`, `gh api` GET), `gh pr merge`, issue comments and every pull
request on your own repositories pass.

A refusal is a PreToolUse deny whose reason starts with `refused: third-party-pr`. A crash of the
reader refuses a command that may open a pull request. A broken `trimwrit-gates.json` also
refuses one, and says to fix the file. A crash on any other command lets it through, recorded.
There is no bypass: no environment variable, no flag, no allowlist, and an empty `owners` with no
gh account refuses every pull request. Enable or disable the plugin with
`claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install no-third-party-pr@trimwrit
```

## Configuration

Section `no-third-party-pr` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key is a loud
error, not a silent pass.

| key | default | meaning |
|---|---|---|
| `owners` | `[]` | GitHub logins whose repositories you may open pull requests on (case-insensitive). Empty: the `user:` of each host block of `~/.config/gh/hosts.yml`; empty too: every pull request is refused |
| `hosts` | github.com, www.github.com, api.github.com, ssh.github.com | hosts where an owner counts; a remote elsewhere is refused |
| `exemption_command` | `[]` | argv of the one command that may allow a third-party pull request (below); empty: no exemption |
| `note` | `""` | appended in parentheses to the rule line of every refusal |
| `reason_extra` | `""` | one paragraph added to every refusal |

### The exemption command

For a target that is not yours, one shape may be allowed: ONE simple command
`gh pr create -R owner/repo --title T (--body B | --body-file F)`, with nothing chained, piped,
redirected, substituted or globbed, and no `--fill`, `--editor`, `--web`, `--template` or
`--recover` (they write a text nobody read). The gate then runs `exemption_command` (a leading
`~` expands) with one JSON object on stdin:

```json
{"repo": "owner/name", "title": "...", "body": "...", "command": "gh pr create ..."}
```

and expects one JSON object on stdout within 10 seconds: `{"ok": true, "reason": "..."}`. Any
other outcome (a non-zero answer that is not that object, garbage, a list, a timeout, a missing
program) is a refusal whose `Ticket:` line says the exemption command failed or gives its reason.

## Proof

`tests/gates/test_no_third_party_pr.py` runs the allowed, refused, compound and fail-closed
matrices against throwaway repositories with hand-added remotes (no network), the hook as a
subprocess (deny, silence, broken configuration, empty owners), and the exemption command through
a fixture script. Nothing there calls the real `gh` or opens a pull request.
