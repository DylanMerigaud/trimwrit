# worktree-kit

One session, one git worktree: on a repository opted in with `.claude/auto-worktree`, a session
starting on the main checkout is told to move into a worktree, edits on the main checkout are
refused, the worktree gets the gitignored config files linked in, and at session end a clean
branch is rebased and pushed when the repository's own gate passes.

- **SessionStart** (`autostart.sh`): on the main checkout of an opted-in repository, injects the
  directive: call EnterWorktree, then run `postenter.sh`. If a configured `roster` lists a
  repository whose marker is missing, it says so first (an anomaly, not a choice).
- **PreToolUse Edit, Write, NotebookEdit** (`guard.sh`): denies a write inside the main checkout
  of an opted-in repository. Allowed: anything under `.claude/`, any path outside the
  repository, anything inside a linked worktree, and any repository where
  `.claude/auto-worktree-off` exists. A denial is counted in `door-refusals.jsonl` (door
  `worktree-guard`).
- **postenter.sh** (run by Claude inside the new worktree): fast-forwards the fresh branch onto
  the remote, makes the hub ignore `.claude/worktrees/`, symlinks the hub's gitignored config
  into the worktree (`.vercel/project.json` at any depth, dot files outside dot directories,
  `*token*.json`, `*credential*.json`, `*secret*.json`, plus the `link_extra` globs; never
  `.env.example`, `.DS_Store`, `node_modules`, `dist`, `build`, `target`, `vendor`), runs
  `postenter_after`, then installs dependencies for the detected package manager.
- **SessionEnd** (`session_end.sh`): `automerge.sh`, then `cleanup.sh`, then `session_end_after`.
  Automerge rebases a clean worktree branch onto the default branch and pushes it, only when five
  conditions hold: it is a worktree under `.claude/worktrees/`, the tree is clean, there is a
  commit the remote lacks, the repository's own `.claude/automerge-gate.sh` exits 0 (no file, no
  merge), and the rebase has no conflict. It never forces a push. Cleanup runs `git worktree
  prune` and records first-seen dates; it never removes a worktree.
- **report.sh** (run by hand): a table of every worktree with REPO, WORKTREE, AGE, UNPUSHED,
  UNMERGED, DIRTY and IGNORED, plus the branches whose worktree is gone. It deletes nothing.

There is no switch: enable or disable the plugin with `claude plugin enable|disable`. A bad
config file is reported on stderr and the defaults apply; no script exits 2 over it.

## Install

```
claude plugin marketplace add DylanMerigaud/trimwrit
claude plugin install worktree-kit@trimwrit
touch .claude/auto-worktree        # in each repository that opts in
```

## Configuration

Section `worktree-kit` of `~/.claude/trimwrit-gates.json` (user) and
`<project>/.claude/trimwrit-gates.json` (project, wins key by key).

| key | default | meaning |
|---|---|---|
| `roster` | `""` | file listing repository toplevels, one per line: the anomaly check of autostart and the repositories `report.sh` walks (empty: the current repository only) |
| `ages_file` | `""` | first-seen dates; empty means `$CLAUDE_PLUGIN_DATA/worktree-ages.tsv`, else `~/.claude/trimwrit-gates/worktree-ages.tsv` |
| `link_extra` | `[]` | globs (matched on the path and on the basename) of extra gitignored files to link, for example `["*.db"]` |
| `postenter_after` | `[]` | argv lists run in the worktree after the links, for example `[["sh", "-c", "make setup"]]` |
| `session_end_after` | `[]` | argv lists run after automerge and cleanup, the SessionEnd payload on stdin |
| `remote` | `"origin"` | the remote to fetch from and push to |

## Proof

`tests/gates/test_worktree_kit.py` builds throwaway repositories (with a local bare repository as
the remote) under a temporary HOME and runs every script against them. No model-run eval: the
hooks are deterministic.
