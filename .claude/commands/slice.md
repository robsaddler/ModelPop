---
description: Slice a model file through the Bambu Studio CLI and report the telemetry
---

Slice the given model file for the Bambu Lab P2S and report what came back.

Follow `.claude/skills/modelpop-slicer-cli/SKILL.md` exactly — especially the argument-quoting rule and
the fact that `result.json` lands in the working directory, not `--outputdir`.

1. Create a fresh throwaway working directory under the scratchpad.
2. Slice with the P2S 0.4 nozzle machine profile and the `0.20mm Standard @BBL P2S` process profile.
3. Read `result.json` and report: return code, predicted print time, layer height, bounding box vs the
   256 mm build volume, whether supports ran, and any `warning_message` **verbatim**.
4. Flag anything that looks like a printability problem — a lot of bridge time, a bbox that will not
   fit, a zero material estimate.
5. Delete the working directory afterwards.

File: $ARGUMENTS
