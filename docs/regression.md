# Regression testing across agent versions

A regression comparison answers one question: is a candidate agent version worse than
the baseline on the same tests? The comparison is deterministic. It uses only the
stored, deterministic check results of two test runs, and no LLM is involved; the
judge's findings are never used by the gate.

## Agent versions

An agent version is a named, fixed `AgentConfig`: model, effort, prompt version and
limits. Versions are defined in [`app/agent/versions.py`](../backend/app/agent/versions.py)
and recorded in `agent_versions` the first time they're used.

| Version | What it is |
|---|---|
| `v1` | Claude Opus 5 with prompt v1 (the default, also used by `/agent/run`) |
| `v1-regressed` | A demo candidate with a deliberately weaker prompt, expected to regress |

Each record stores the config and a `config_hash`. The hash covers the config **and the
prompt texts it selects**, so editing a prompt changes it. A name never changes meaning:
if the code defines `v1` differently from what's recorded, the sync refuses with `409`
(CLI: an error) and the change has to be given a new version name. Test runs reference
their version (`test_runs.agent_version` → `agent_versions.name`) and also store the
exact config they ran with.

Runs from before Phase 8 carry a free-text label such as `default`. The migration
records each label as a version "known only from old runs" (`defined_in_code = false`).

## Pairing and categories

Results from the two runs are paired by variant id (e.g. `cancel-shipped-order:normal`).
Every result stores the content hash of the scenario it was graded against, and the
version of the deterministic check set that graded it. A pair is only compared when
both match and the check-set version is known. Otherwise the test or the grading
changed, and the difference isn't blamed on the agent.

| Category | Meaning | Counted by the gate |
|---|---|---|
| `regression` | Worse in one observation: a lower verdict (pass > fail > error) or a newly failing critical check | yes, and a rerun is recommended |
| `confirmed_regression` | Still worse in most reruns | yes |
| `improvement` | Better: a higher verdict, or a critical failure fixed without a new one | yes |
| `unchanged` | Same verdict and same critical failures | yes |
| `noisy` | Reruns disagreed with the first observation | no |
| `scenario_changed` | Scenario content hash differs between the runs | no |
| `checks_changed` | Graded by different check-set versions, or by an unknown one (results from before versioning) | no |
| `missing_in_candidate` / `new_in_candidate` | Only one run has the variant | no |

Each variant row also lists newly failing and fixed checks of every severity, its
latency and cost deltas, and a one-line explanation.

## Metric deltas

All metrics are computed only over the **gated** variants (paired, same scenario
content, not noisy), so that differences in what was selected don't move the numbers.

- **Rates**, in percentage points, each with its `passed/total`: task success, policy
  compliance, intent, tool selection, tool arguments, escalation accuracy, failure
  recovery, crash rate.
- **Values**, as an absolute change and a percentage: mean and p95 latency, estimated
  cost, LLM calls, tokens (input, output, cache write, cache read), tool calls, forbidden
  tool violations, missed and unnecessary escalations.

## The gate

Default thresholds are listed below. All of them are configurable per comparison, and
each report stores the thresholds it was judged with.

| Rule | Default | Level |
|---|---|---|
| A gated variant newly fails a critical check (`fail_on_new_critical`) | on | **fail** (or warn if turned off) |
| Task success drops by **more than** `max_task_success_drop_pp` | 5 points | **fail** |
| Estimated cost rises by **more than** `max_cost_increase_pct` | 25% | warn |
| Mean latency rises by **more than** `max_latency_increase_pct` | 25% | warn |
| No variant could be compared | – | **fail** |

There are also warnings for:

- regressions without a new critical failure;
- regressions seen only once (rerun recommended);
- noisy variants;
- changed scenarios;
- results graded by different or unknown check sets;
- baseline variants the candidate didn't run;
- cost that can't be estimated (a model with no known price).

The status is `fail` if any rule fails, otherwise `warn` if any rule warns, otherwise
`pass`. Thresholds are exclusive: a drop of exactly 5.00 points passes. The arithmetic
uses exact fractions, so a drop of 1/3 isn't affected by float rounding.

## Reruns: noise vs. real regressions

One run per variant is the default. A flip can be model noise, so a report can be
followed by a rerun:

```
POST /comparisons/{id}/reruns   {"repetitions": 3, "sides": "both", "variant_ids": [...]}
```

By default, the variants rerun are those in category `regression`. Each side repeats
the chosen variants `repetitions` times, as new test runs linked to the rerun
(`test_runs.rerun_id`, `rerun_side`, `repetition`). Each side reuses **its original
run's exact config**, and no judge runs.

The result is a **new revision** of the report. The original report is never changed.
Every revision uses all the rerun observations collected so far, and stores the
evidence for each reclassified variant: every observation (test run, verdict, critical
failures, whether it was counted), the counts, and the explanation.

| First result | After reruns |
|---|---|
| `regression` | `confirmed_regression` if most candidate reruns are again worse than the original baseline result **and** fewer than half of the baseline reruns are. Otherwise `noisy`: either the failure didn't reproduce, or the baseline fails the same way just as often. |
| `improvement` | Stays `improvement` under the mirrored rule, otherwise `noisy` |
| `unchanged` | `noisy` if any rerun differs |

Reruns graded against different scenario content, or by a different check set, are
recorded but not counted. Noisy
variants drop out of the gate and are listed as a warning. Reruns call Claude: the API
and the CLI estimate the cost from what the same variants cost in the original runs, and
the CLI asks before it starts.

## Check-set versions

`CHECK_SET_VERSION` in [`app/evaluation/checks.py`](../backend/app/evaluation/checks.py)
names the deterministic grading rules:

| Version | What it is |
|---|---|
| `checks-v1` | The original Phase 5 checks |
| `checks-v2` | Adds `tools.lookup_before_action` |

Results stored before check sets were versioned have a null version, shown as
`unknown`. Every test run (`test_runs.check_set_version`) and every result
(`evaluation_results.check_set_version`) records the version that graded it. Reports
list the versions found on each side, and each variant row stores both of its versions.
The version is shown in `/test-runs`, `/test-runs/{id}/results`, `/comparisons` and the
CLI's `show`, `compare` and `report`.

The version is bumped by hand whenever a change could alter a check result or verdict.
A fingerprint test over `checks.py` and `results.py` fails on any edit to them, so every
change to the grading code comes with an explicit decision about the version.

## API

| Endpoint | Purpose |
|---|---|
| `GET /agent-versions`, `GET /agent-versions/{name}` | Versions in code plus those known from old runs |
| `POST /test-runs` `{"agent_version": "v1", ...}` | Run a selection with a version (unknown version → 422) |
| `POST /comparisons` `{"baseline_run_id", "candidate_run_id", "thresholds"?}` | Compare and store (201). Unknown run → 404, unfinished → 409, same run / nothing in common → 422 |
| `GET /comparisons?baseline_run_id=&candidate_run_id=` | List reports, newest first |
| `GET /comparisons/{id}` | Full report: gate, deltas, variants, metrics, ids of all revisions |
| `GET /comparisons/{id}/gate` | Gate status and reasons, plus the human-readable text |
| `GET /comparisons/{id}/variants?category=` | Variant rows, optionally by category |
| `POST /comparisons/{id}/reruns`, `GET /comparisons/{id}/reruns` | Start a rerun (202, paid) / list reruns and their result revisions |

## CLI

```bash
.venv/bin/python -m app.evaluation versions
.venv/bin/python -m app.evaluation run --agent-version v1-regressed --scenario cancel-shipped-order
.venv/bin/python -m app.evaluation compare <baseline-run> <candidate-run> [--json]
    [--max-task-success-drop 5] [--max-cost-increase 25] [--max-latency-increase 25]
    [--warn-on-new-critical]
.venv/bin/python -m app.evaluation report <report-id> [--json]
.venv/bin/python -m app.evaluation rerun <report-id> --repetitions 3 --sides both   # paid; asks
```

`compare` exits `1` when the gate fails, `0` for pass or warn, and `2` when the runs
can't be compared, so it can be used as a CI step.
