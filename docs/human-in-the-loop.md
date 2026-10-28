# Human-in-the-loop approvals

Some actions are too risky for the agent to take on its own. Today that means refunds
over $500: `create_return` refuses them with `approval_required`. When that happens the
agent doesn't hand off or give up. It **pauses**. The paused conversation waits for a
support team member to decide, then **resumes the same execution**.

## Flow

```
POST /agent/run ──> agent ─> run_tools ── create_return refused: approval_required
                                │
                                ▼
                         request_approval   insert human_approvals row (pending)
                                │
                                ▼
                         await_decision     interrupt(): checkpoint saved, request returns
                                            status=pending_approval, approval_id
      ··· time passes, separate HTTP request ···
POST /approvals/{id}/approve|reject
                                │           record decision, resume with Command(resume=…)
                                ▼
                         apply_decision     approved: claim, then run the tool once
                                │           rejected: substitute a "declined" tool result
                                ▼
                              agent         Claude writes the follow-up for the customer
```

- **Checkpointing.** LangGraph's `PostgresSaver` stores the graph state in Postgres.
  The thread id is the `execution_id`, so any API process can resume any paused
  execution. Its tables (`checkpoints`, `checkpoint_writes`, …) are created by
  `saver.setup()` and excluded from Alembic autogenerate.
- **Customer reply while paused.** The customer is told the refund is under review.
  Any other tool results from the same model turn are held back and delivered together
  with the decision, so Claude gets one complete set of results.
- **Follow-ups.** `POST /agent/run` with an `execution_id` continues an existing
  conversation. While that conversation is paused it returns `409`. If the
  `execution_id` belongs to another customer it returns `404`.

## `human_approvals`

One row per paused action, unique on `(execution_id, tool_use_id)`:

- **What was requested:** tool name, arguments, customer, order, refund amount, risk
  level, reason and the customer's reason.
- **Decision:** `status` (`pending` → `approved` | `rejected`), `decided_by`,
  `decision_note`, `decided_at`.
- **What happened afterwards:** `action_status` (`not_run` → `executing` →
  `succeeded` | `failed`), `action_result`, `resumed_at`.

## Safety guarantees

| Guarantee | How |
|---|---|
| An approved action runs at most once | `apply_decision` claims it with `UPDATE … SET action_status='executing' WHERE status='approved' AND action_status='not_run'`; only the request that wins the claim runs the tool. A later resume reports the stored result instead. |
| A rejected action never runs | The tool is only called on the approved path. `ToolContext.approved` is set by `apply_decision` alone. |
| Duplicate or concurrent decisions are safe | The decision is a conditional update (`WHERE status='pending'`), and each approval is serialized by a Postgres advisory lock held for the whole resume. A second decision gets `409`. |
| A crashed resume can be finished | If the decision was recorded but `resumed_at` is still empty, repeating **the same** decision resumes the execution. A different decision gets `409`. |
| The pause is resumable only once | Resuming an execution that has no pending interrupt raises `NotPaused` (`409`). |

If Claude is unavailable for the follow-up, the execution escalates (`llm_unavailable`)
with a handoff message, as on any other turn. The action has already run and is not
repeated. **Known gap:** if the process dies after the action ran but before the
follow-up is written, the decision can't be repeated, because `resumed_at` is already
set. A person then has to tell the customer the outcome, using the approval's
`action_result`.

## API

| Endpoint | Purpose |
|---|---|
| `GET /approvals?status=pending` | The review queue (newest first; `limit` up to 200) |
| `GET /approvals/{id}` | One approval with its decision and outcome |
| `POST /approvals/{id}/approve` | Body `{"reviewer": "...", "note": "..."}`; returns the approval plus the resumed execution's status and reply |
| `POST /approvals/{id}/reject` | Same body; the agent tells the customer it was declined |

There's no staff authentication yet: `reviewer` is taken from the request body.

## Tracing

The pause and the resume are recorded in **one** trace, which keeps the same
`execution_id`. The resume continues the event sequence and turn of the stored trace.

| Event | Where |
|---|---|
| `approval_requested` | approval id, risk level, refund amount |
| `execution_paused` | approval id; followed by `response_finalized` (status `pending_approval`) |
| `execution_resumed` | approval id (first event of the resuming request) |
| `approval_decision` | decision and reviewer; status `ok` or `rejected` |

The tool call appears twice in `tool_calls`. The first entry is `rejected`, from when
approval was required. The second is the real execution after approval.

## Evaluation

The ground truth for a high-value refund is `outcome: approval_requested` with
`final_status: pending_approval`. The checks treat it as a human handoff, so
"missed a required escalation" also covers failing to pause. Scenario runs delete their
checkpoint thread afterwards.
