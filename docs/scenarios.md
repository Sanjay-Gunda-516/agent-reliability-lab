# Writing scenarios

Scenarios are the Reliability Lab's test cases. Each one describes a customer
conversation and what a correct agent does in it. They live in
`backend/scenarios/*.yaml`, are reviewed like code, and are synced into the database
with `python -m app.scenarios sync`.

```bash
cd backend
.venv/bin/python -m app.scenarios check   # validate every file and print a summary
.venv/bin/python -m app.scenarios sync    # load into test_scenarios / scenario_variants
```

## Anatomy

```yaml
- id: cancel-shipped-order            # stable slug; results are keyed by it
  title: Refuse to cancel an order that has shipped
  tags: [edge-case, policy]
  customer_id: 1001                   # the signed-in customer (demo store, app/seed.py)
  variants:                           # persona -> message, or a list of messages for multi-turn
    normal: "Please cancel order #8821."
    angry: "Cancel #8821 immediately. I don't care that it's already on its way."
  expected:
    behavior: >                       # what a correct agent does, in words
      Explains that order 8821 has shipped and can't be cancelled, and offers a return.
    outcome: cancellation_refused
    final_status: completed           # or escalated (then escalation_reason is required),
                                      # or pending_approval (with outcome: approval_requested)
    intent: cancel_order              # intent of the first turn; omit to skip grading it
    must_call: []                     # calls that must happen; args are a subset match
    must_not_call:
      - {tool: cancel_order}          # result: any (default) = must not even be attempted
    state:
      orders: {8821: shipped}         # order statuses after the conversation
      returns: {}                     # order id -> whether a return exists afterwards
    response:
      must_mention: []                # case-insensitive substrings; use sparingly
      must_not_mention: []
```

Optional sections:

- `setup.orders` adds orders to the demo store for this scenario, with times relative
  to the run (`placed_ago: 23h`). Use new order ids, not ones from the seed data.
- `faults` injects failures: `tool_timeout`, `database_error` or `tool_crash` on a
  named tool, or `llm_unavailable` for Claude. `times: N` fails the first N attempts
  then recovers; omit it to fail every time.

## Rules for ground truth

1. **Write expectations from the store's published policy and the demo data, never by
   running the agent's rule code.** If a rule has a bug, a scenario computed from that
   rule would agree with the bug. `tests/unit/test_scenario_independence.py` fails the
   build if `app/scenarios` imports the rule or tool modules.
2. **Variants change wording, never facts.** All variants share one ground truth. If a
   message drops information (e.g. no order number), it's a different scenario.
3. **Prefer state and tool-call checks over text checks.** What happened to the order
   is objective. Use `response` checks only for facts that must appear or data that
   must never leak, and describe everything else in `behavior` (Phase 5 grades
   behaviour that can't be checked deterministically with a narrow LLM judge).
4. **Pick `must_not_call` strictness deliberately.** `result: any` means attempting the
   call is already wrong (cancelling an order the agent can see has shipped).
   `result: success` allows an attempt the system will refuse, for cases where the tool
   is how the agent finds out (e.g. the 24-hour window).
5. **Fault scenarios need one sensible answer.** If an alternative tool could still
   answer the question, fault that tool too.

Editing a scenario changes its content hash, so later comparisons can tell "the agent
changed" from "the test changed".
