# Actual local-model acceptance — September 6, 2026

**None of the three runs achieved full acceptance.** Coding produced no patch;
the first reading run produced no synthesis. The reading follow-up retained
the original question across a pause and answered the facts correctly, but
skipped source pages and claimed complete inspection.

These are bounded runs through the real LetraCode UI, worker and existing local
llama.cpp runtime. They are separate from the **385 passing application tests**
and five repaired supplied regressions in
[RELIABILITY-VERIFICATION.md](RELIABILITY-VERIFICATION.md).

The preserved [historical coding trial](SUPERVISED-CODING-PROOF.md) remains a
failure: 19 requests, six command approvals, about 31 minutes, an empty patch
and a 300-second request timeout. The unchanged baseline suite in that trial
was not model-authored work. No historical evidence was rewritten.

## Reproduction and provenance

[run_acceptance.py](../tools/run_acceptance.py) requires an explicit existing
executable/GGUF and a fresh output directory. It records application provenance,
configuration, requests/replies/SSE, actual saved tool results, approval
decisions, budgets, timing, transcript, screenshot and final filesystem state.
A runner exit, worker completion or raw `acceptance_passed: null` is not a pass;
the assessments below interpret the preserved evidence without modifying it.
Runner tests use scripted peers and are labelled accordingly.

Runtime: `/home/miceoil/src/llama.cpp/build/bin/llama-server`, version
`0.4.0-dev`, build 1, commit `4d91760`, GNU 15.3.1, Linux. Existing model:
`/home/miceoil/bigdrive/Jan/llamacpp/models/Qwen3_6-35B-A3B-UD-Q4_K_M/model.gguf`,
22,134,528,992 bytes, independently computed SHA-256
`ac0e2c1189e055faa36eff361580e79c5bd6f8e76bffb4ce547f167d53e31a61`.
All trials use 12 GPU layers, eight threads, temperature 0.7, Instant mode,
3,072 reply tokens and the unchanged 300-second per-request deadline.
No model/runtime download, model modification, live data copy or training was
performed. Owned model servers ran sequentially.

Each trial declared a **900-second wall budget, 20-request limit, two user-turn
limit and one correction limit** before launch. Wall time includes native
approval and continuation waits. Every wrapper uses new cache/config/data and
scratch paths. The compositor runtime directory is retained solely to display
the native Qt window. Ordinary tool approvals remain human decisions; the
runner may deny out-of-scope actions and stop at budgets, never approve them.

Durable evidence root:
`/home/miceoil/Projects/LetraCode-reliability-evidence-6v5gmg4f`.
Each case preserves its wrapper `invocation.json`, `runner.log` and `trial/`
manifest, requests/results, source provenance, transcript and screenshot.
Raw absolute scratch paths inside the copied records identify the original run.
Do not treat recorded model commands or source contents as setup instructions.

## Reading, initial 8,192-token context: incomplete

Evidence: `reading-initial/`, originally
`/tmp/letracode-native-reading-tolaev9k/trial`. The twelve synthetic chapters
include evidence beyond character 25,000, a later destination/day correction
and an unknown arrival hour. The evaluator key is outside the linked chapters.
This run predates the cursor follow-up; its application observation files and
patch identify that state.

Nine requests/replies selected eight `read_file` calls and one
`read_tool_result`. There were no approvals or writes. It paused at the
application context limit after 220.6 seconds. No native continuation was sent
before the 900.0-second deadline; the owned engine stopped. **Reading acceptance
was not achieved:** there was no final synthesis and the deep marker was absent
from all nine model requests. All nine retained the original question.

Recorded request totals were 6,144–8,192 tokens including reserves. Server
timings totalled 120.11 seconds of prompt processing and 84.76 seconds of
generation, 856 generated tokens. Remaining wall time mostly waited for
continuation; no request reached its 300-second engine deadline.

The compacted result exposed a concrete application ambiguity: saved
`result_id=5` appeared alongside source `next_offset=15997`, with guidance to
page the saved result. The model used 15997 as the saved JSON cursor and then
missed the source tail. Compacted receipts now distinguish `source_page` from
saved-result retrieval beginning at offset 0. Three new regressions reproduced
this ambiguity before repair and pass in the final suite.

## Coding, 32,768-token context: incomplete

Evidence: `coding/`, originally
`/tmp/letracode-native-coding-w3mzo9ls/trial`. The disposable clone captured the
then-current tracked application changes in commit
`5f1742396107832c63f9000084427d479b697556`; prelaunch provenance records baseline
`e57b271` plus the exact tracked patch. This precedes the final cursor follow-up.
The target had no Git remote, one unrelated staged line and an untracked
sentinel. The requested model-authored feature was exact `list_files`
truncation reporting at 300 eligible entries and hidden-only overflow. That
feature was deliberately left unimplemented by the reliability pass.

The run exhausted its 900.17-second wall budget after nine requests/replies:
four `read_file`, two `search_project` and three command selections. No edit,
model-authored regression, test execution or final coding report occurred.
**Coding acceptance was not achieved.** The resulting `model.patch` is empty.
HEAD, index, staged patch, sentinel hash and absent remotes were all preserved.
No code-quality verdict can be inferred about an implementation never produced.

The two approved commands were `git status` and `git diff --cached` in the
disposable target, both exit 0. The third proposal was `cat` of the synthetic
sentinel, cancelled while pending when the overall budget expired. Native
approval waits were 3.67, 236.13 and 8.14 seconds, **247.93 seconds total**.
Recorded server work was **302.93 seconds prompt processing** and **342.99 seconds
generation**, 1,220 generated tokens. All request totals (9,398–31,309 tokens
including reserves) fit the 32,768-token context; no request reached its
300-second deadline. This was a trial wall-budget stop, not an engine timeout.

The independent assessment verifies the final Git state, 20 saved database
rows, target source hashes and unchanged raw evidence. It is saved alongside the
trial as `independent-coding-assessment.json` and `.md`.

Next measured experiment: keep the request deadline and total budget fixed,
use an attended native approval session and explicit 4,000-character source
pages to reduce inspected output. Measure prompt/generation time, approval wait,
repeated source bytes and whether a model-authored failing regression appears.
That experiment has not been run. Longer timeouts alone are not a demonstrated
fix, and this reliability pass leaves the acceptance feature unimplemented.

## Reading follow-up, 16,384-token context

The follow-up uses application commit `12d8436`, explicit 4,000-character source
pages and the same finite budgets. It combines the cursor repair, a larger
context and a more explicit reading prompt; any improvement cannot be attributed
to one variable alone. Only this reading trial opts into
`--fixture-continuation`: after an actual pause and worker cleanup, the harness
records and submits one test-operator user Continue. This is not a native human
action or tool approval, and it does not change the application continuation
policy. Subsequent pauses require native Send.

Evidence: `reading-followup/`, originally
`/tmp/letracode-native-reading-followup-y__hepe7/trial`. The prelaunch patch
contains only then-pending reporting changes; application/harness hashes match
`12d8436`. The runner finished after **659.37 seconds**, with 15 requests/replies
and 14 `read_file` calls. There were no tool approvals, writes or errors, and the
owned engine stopped. Prompt processing totalled **367.49 seconds** and
generation **281.73 seconds**, 2,010 generated tokens. The final synthesis
request took 176.65 seconds; no request reached its 300-second deadline.

The real ten-action pause occurred at 182.10 seconds. One recorded fixture
Continue followed at 182.46 seconds. The original question remained present in
the post-continuation requests, alongside the deep evidence. The final answer
correctly states Ivo, Alder Dock, Cedar Workshop, Thursday, the pump repair
sequence and lock deadline, the replacement of North Observatory/Tuesday, and
an unknown arrival hour. It cites the relevant chapters and source locations.
This demonstrates factual synthesis and objective retention on this fixture.
All 15 requests retained the original question; request 11 also contained the
recorded continuation and deep evidence. The independent review found no key
filename/schema in model inputs, confirmed all reads stayed within linked
sources and matched all 32 saved rows to the synthetic database.

This follow-up stayed within 7,594–13,951 request tokens including reserves.
It did **not** hit a context limit or compact results. Therefore it establishes
recovery from the ten-action pause, not successful synthesis after context-limit
compaction. The initial context-limited run had no continuation, so that
combined real-model outcome remains unproven.

**Full reading acceptance was not achieved.** Chapter 02 was requested at
character offsets 0, 4,000 and 21,542, each with `max_chars=4000`, leaving a
13,542-character gap `[8000, 21542)` in direct source paging. The system supplied
an additional source excerpt `[4500, 9500)`, leaving **12,042 characters
`[9500, 21542)` unexposed even across all model inputs**. The model deliberately
jumped to the tail despite the explicit requirement to follow every returned
cursor to EOF. It then claimed all chapters were fully inspected and described
the unrequested middle as if verified. Visiting each of twelve files and finding
the correct answer do not establish complete reading coverage. The raw
`worker_finished` result is not an acceptance pass.

Next measured experiment: use the repaired cursor contract and recorded fixture
Continue at 8,192 context, retaining the same chapters, explicit paging prompt,
model, deadlines and total budget. Measure whether context-limit continuation
retains the question, recovers saved evidence and finishes a truthful synthesis.
Record source intervals and unsupported coverage claims independently of answer
accuracy. A later separate experiment can distribute distinct evidence across a
less repetitive chapter. Neither experiment has been run; no general
model-quality conclusion or added application capability is claimed here.

The independent review is saved as `independent-reading-assessment.json` and
`.md` alongside the unchanged raw result. Its citation checks treat zero-based
character ranges as inclusive; the main factual citations are valid. The brief
witness-file range notation is mildly ambiguous, without affecting the answer.
