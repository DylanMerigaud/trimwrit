---
name: id-shown-bare
description: 'A session closes with two review-queue items waiting on the user and a fix merged; the queue file keys the items INT-0412 and INT-0438 and the fix is commit 4f2a9c1e; the user reads the closing message on a phone, without the tools'
runs: 3
max_turns: 4
---

You are Claude Code, closing a long working session for the user, who is away and will read your final message on a phone, without the terminal, the files or the tools. During the session you fixed the timeout of the roster import script: the fix is commit 4f2a9c1e, merged on main, and the 81 tests of the suite pass. Two items of the user's review queue wait on the user, exactly as the queue file lists them:

INT-0412  field test of the dental-lab bet: 38 of 60 first mails sent, stopped by the sending mailbox's daily cap of 38; the user chooses between raising that cap to 50 or moving the 22 remaining mails to a second mailbox.
INT-0438  call slot proposed by Noah Brandt, head of engineering at Eve, for Thursday at 15:00 Paris time: yes or no.

From the phone, the user answers a queue item with `answer <item> --go` or `answer <item> --skip`.

Write your final message to the user and end the turn.
