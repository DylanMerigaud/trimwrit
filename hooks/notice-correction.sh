#!/bin/sh
# Silent on every normal prompt. On a prompt that reads like a correction, it prints one line
# reminding Claude that the roast skill exists.
#
# NOT WIRED BY DEFAULT, and the way that is achieved matters. Claude Code AUTO-DISCOVERS
# hooks/hooks.json at a plugin root, with no manifest entry needed, so the first version of
# this plugin shipped the hook live while its own README said it was off. `claude plugin
# details trimwrit` reported "Hooks (1) UserPromptSubmit" and that is how it was caught. The
# config now lives at hooks/hooks.disabled.json, which auto-discovery ignores.
#
# To turn it on: `mv hooks/hooks.disabled.json hooks/hooks.json` in your installed copy. Read
# this script first. It runs on every prompt you type, forever.
#
# The reason it ships disabled is the same reason the tool exists. A hook that fires on a
# keyword list will misfire, and a reminder that misfires trains you to ignore reminders. The
# skill description already triggers on these phrases without any hook at all; this is here for
# people who measured that and found it was not enough.
set -eu
payload=$(cat 2>/dev/null || true)
prompt=$(printf '%s' "$payload" | tr 'A-Z' 'a-z')

case "$prompt" in
  *"j'aime pas"*|*"jaime pas"*|*"non pas ca"*|*"c'est pas ca"*|\
  *"i don't like"*|*"i dont like"*|*"that's not what i asked"*|\
  *"thats not what i asked"*|*"you did it again"*|*"stop doing"*|*"wrong again"*)
    printf '%s\n' "The user is correcting you. Use the trimwrit roast skill: name the mechanism in one line, then record it with trimwrit log."
    ;;
  *) : ;;
esac
exit 0
