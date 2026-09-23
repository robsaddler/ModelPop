---
description: Run a timeboxed spike and write it up in docs/research
---

Run a throwaway spike to answer one question, then write it up.

1. Restate the question as a single falsifiable claim before writing any code.
2. Build the smallest possible throwaway program in the scratchpad, not in the solution.
3. Run it. Record what actually happened, including exact error text.
4. Write `docs/research/spike-<topic>.md` with: the claim, the result (pass/fail), the working
   invocation or code if it passed, every trap you hit, and what it means for the architecture.
5. **Write it up whether it succeeded or failed.** A documented failure has done its job.
6. If the result contradicts an ADR, say so explicitly and propose the ADR change. Do not quietly
   proceed as if the ADR still holds.

Spike: $ARGUMENTS
