# Arena judging

Purpose: blind pairwise judging of competing candidates, with rubric, bias controls and aggregation. Serves SKILL.md phase 3 item 8, which runs it only when round 1 yields three or more materially different approaches; skip it when the proposals are variants of one design.

## Blinding
- The President copies each `r1-proposal-<seat>.md` to `blind/C-<code>.md`, where `<code>` is a random 3-letter code. Seat names, model names and self-references are redacted to `[seat]`.
- The code to seat map is held outside the decision directory until all verdicts are in, then published in `judging-results.md`.
- Judges read only `blind/`. Opening the unblinded files before submitting is a protocol breach.

## Assignment
- Every unordered pair is judged twice, by two different judges, with the positions swapped (A/B in one, B/A in the other).
- A judge never judges a pair that contains its own candidate. If you recognise your own text in a pair, say so and abstain on that pair.

## Rubric
Score each candidate 1 to 5 per criterion; weights in brackets. Fix the criteria and weights in the judges' brief before any judging. The K-2036 skill-design rubric is the worked example:
1. Effectiveness [25]: would following it reproduce the reference results?
2. Failure prevention [25]: does a mechanical step (a check or command, not a reminder) catch each known failure?
3. Clarity [15]: can a fresh agent execute it without guessing? Are the phases, gates and owners unambiguous?
4. Economy [10]: tokens, wall clock and machine load are proportional to the job, with model tiering and a "skip the arena when small" path.
5. Safety and gates [15]: card discipline, owner gate, independent review, no self-approval, no invented tools, repo doctrine.
6. Portability [10]: generic for the whole topic class, while keeping the hard-won specifics in references.

## Bias controls
- Length is not quality. Penalise text that does not change what an agent does. Each blind file states its word count, so judge content per word.
- Judge the order you were given; do not reorder. Your swapped twin is judged by someone else.
- Before choosing, write the strongest argument for the candidate you are leaning against.
- Never judge your own candidate; strip author labels.

## Verdict format (one block per pair, in `judging-<judge>.md`)
```
PAIR: C-xxx (A) vs C-yyy (B)
SCORES A: e=_ f=_ c=_ $=_ s=_ p=_ | weighted=_
SCORES B: e=_ f=_ c=_ $=_ s=_ p=_ | weighted=_
STEELMAN-AGAINST-LEAN: <one line>
WINNER: A | B | TIE
STEAL: <the best idea from the loser, one line>
```
The weighted score is sum(score x weight)/5, so the maximum is 100.

## Aggregation
The President computes win rates and a Bradley-Terry ranking, with ties counted as half a win each way, plus the mean weighted score. The top candidate becomes the base for synthesis. The STEAL lines become the merge list.

## Corrections learned in K-2036 round 2
- Recompute every block's weighted score from its six criterion scores on the 0 to 100 scale; the winner is the higher score, and a gap of 2 points or less is a tie.
- A candidate that names a tool, script or flag that does not exist scores safety 2 or lower, and the block names the invented item.
- Fact checks (does the tool exist, is the claim sourced) go to a deterministic step or a Sonnet or Opus judge, never to a light-tier judge: in that round the Sonnet judges caught every invented tool and the light-tier judges missed them.
- Read the judges' blocks for protocol errors (wrong scale, abstaining on a pair that is not the judge's own) and have the judge redo them.

## Sources (what each contributed)
- Chiang et al., Chatbot Arena, https://arxiv.org/abs/2403.04132: pairwise ranking with Bradley-Terry and position randomisation.
- Ye et al., Justice or Prejudice? (CALM), https://arxiv.org/abs/2410.02736: judge biases, position and verbosity first; hence swapped double judging, word counts shown, a steelman line.
- Wataoka et al., Self-Preference Bias in LLM-as-a-Judge, https://arxiv.org/abs/2410.21819: judges favour their own text; hence no self-judging.
- Identity bias in debate, https://arxiv.org/html/2510.07517v2: anonymising reduces sycophancy and self-bias.
- karpathy/llm-council, https://github.com/karpathy/llm-council: first opinions, anonymised review, chairman synthesis.
