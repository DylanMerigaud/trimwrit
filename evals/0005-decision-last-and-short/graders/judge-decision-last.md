---
type: llm
name: judge-decision-last
criteria: The reply ends with a short closing block, at most about six lines, that names the options and states which one the writer picks. PASS if the final block is short and carries an explicit pick. FAIL if the pick is buried earlier in the body, if the reply ends with detail or caveats instead of the decision, or if it lists options without choosing one.
focus: last_message
---

From correction 0005: you gave me a menu with no pick, that hands the work back to me
