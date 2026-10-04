# main-checkout-guard

A PreToolUse hook on `Bash` that refuses destructive git on the main checkout of a repository
opted into worktree isolation. Other sessions and crons keep uncommitted work in that checkout;
`git checkout -- .` or `git reset --hard` there wipes it, and an Edit/Write guard cannot see it
because git runs through Bash.

A repository is protected when its toplevel carries `.claude/auto-worktree`, or when its path is
listed in `protected_roots`. Its MAIN checkout is the main working tree (git-dir equals
git-common-dir), never a linked worktree.

Refused on the main checkout: checkout with paths or over local changes, any branch switch away
from the default branch, `restore` unless `--staged` only, `reset --hard/--merge/--keep` and any
reset that moves HEAD, `clean` unless a dry run, stash writes and drops, `commit`, `merge` and
`pull` unless `--ff-only`, `rebase`, `cherry-pick`, `revert`, `am`, `bisect`, and the plumbing
equivalents. Refused anywhere in the repository, worktrees included: deleting, renaming or
force-moving the default branch, a force push whose destination is the default branch, a push
that deletes it, `filter-branch` and `filter-repo`.

The whole command is read as shell: `&&`, `||`, pipes, subshells, `$(...)`, `bash -c`, `eval`,
heredocs, `env` and `xargs` prefixes, `cd` tracking, `-C`, `--git-dir`, git aliases. When a
session runs inside a protected repo and the target cannot be expanded (a `cd` to a variable),
a verb that would be refused is refused: the gate fails closed. A read-only git is never refused.

A refusal is exit 2 with the reason on stderr. A crash or a timeout lets the call through and says
so in a systemMessage. There is no bypass: no environment variable, no marker file, no flag, and
`.claude/auto-worktree-off` does not disarm it. Enable or disable the plugin with
`claude plugin enable|disable`.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install main-checkout-guard@trimwrit
```

## Configuration

Section `main-checkout-guard` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key). An unknown key is a loud
crash, not a silent pass.

| key | default | meaning |
|---|---|---|
| `protected_roots` | `[]` | extra repository paths protected without the marker (`~` expands) |
| `default_branch` | `"main"` | the branch the door keeps safe |
| `note` | `""` | one sentence appended to every refusal ("... This door exists since.") |
| `remedy_main_checkout` | worktree, pull request, `git pull --ff-only` | the "Do instead" line for the main checkout |
| `remedy_default_branch` | merged pull request only | the "Do instead" line for default-branch rewrites |

## Proof

`tests/gates/test_main_checkout_guard.py` pipes PreToolUse payloads into the script against
real throwaway repositories (a marked repo, its linked worktree, an unmarked repo) and reads the
exit code and stderr.
