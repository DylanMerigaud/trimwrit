---
name: 0005-decision-last-and-short
description: you gave me a menu with no pick, that hands the work back to me
tags: [decision-last]
runs: 3
max_turns: 3
---

We have a Postgres table that has grown to 400 million rows and reads are getting slow. Should we partition it, add a read replica, or move the cold rows to a separate archive table? Give me the reasoning.
