import { CheckCircle2, PauseCircle, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ApiError } from "@/api/client";
import { useApprovals, useDecideApproval, type Decision } from "@/api/queries";
import type { Approval, DecisionResponse } from "@/api/types";
import { KeyValues, Mono } from "@/components/common/Details";
import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { EmptyState, QueryState } from "@/components/common/QueryState";
import { StatusBadge } from "@/components/common/StatusBadge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { dateTime, humanize, shortId, usd } from "@/lib/format";
import { approvalStyle, executionStyle } from "@/lib/status";
import { cn } from "@/lib/utils";

const REVIEWER_KEY = "arl.reviewer";

// The reviewer name is a per-browser convenience; storage may be unavailable.
function savedReviewer(): string {
  try {
    return localStorage.getItem(REVIEWER_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveReviewer(name: string) {
  try {
    localStorage.setItem(REVIEWER_KEY, name);
  } catch {
    // Ignore: remembering the name is optional.
  }
}

function describeAction(a: Approval): string {
  const order = a.order_id !== null ? ` for order ${a.order_id}` : "";
  return a.tool_name === "create_return" ? `Start a return${order}` : `${humanize(a.tool_name)}${order}`;
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.detail : "The decision couldn't be sent.";
}

function Outcome({ result }: { result: DecisionResponse }) {
  const approved = result.approval.status === "approved";
  return (
    <div role="status" className={cn("rounded-lg border px-4 py-3", approved ? "border-success/30 bg-success-soft" : "border-critical/30 bg-critical-soft")}>
      <div className={cn("flex items-center gap-2 text-sm font-medium", approved ? "text-success-strong" : "text-critical-strong")}>
        {approved ? <CheckCircle2 aria-hidden className="size-4" /> : <XCircle aria-hidden className="size-4" />}
        {approved ? "Approved" : "Rejected"}: the conversation resumed and is now {executionStyle(result.execution_status).label.toLowerCase()}.
      </div>
      <p className="mt-2 text-xs text-text-muted">Reply sent to the customer:</p>
      <p className="mt-1 text-sm whitespace-pre-line text-navy">{result.final_response}</p>
      <Link to={`/traces/${result.execution_id}`} className="mt-2 inline-block text-xs font-medium text-blue-strong hover:underline">
        View the resumed execution →
      </Link>
    </div>
  );
}

interface CardCallbacks {
  /** The decision went through; the card leaves the pending list on refresh. */
  onDecided: (result: DecisionResponse) => void;
  /** Someone else got there first (409); also shown above the list. */
  onConflict: (message: string) => void;
}

function PendingCard({ approval, onDecided, onConflict }: { approval: Approval } & CardCallbacks) {
  const [reviewer, setReviewer] = useState(savedReviewer);
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState<Decision["decision"] | null>(null);
  const decide = useDecideApproval();
  const reviewerId = `reviewer-${approval.id}`;
  const noteId = `note-${approval.id}`;
  const ready = reviewer.trim().length > 0 && !decide.isPending;

  const submit = () => {
    if (!confirming) return;
    saveReviewer(reviewer.trim());
    decide.mutate(
      { id: approval.id, decision: confirming, reviewer: reviewer.trim(), note },
      {
        onSuccess: onDecided,
        onError: (error) => {
          if (error instanceof ApiError && error.isConflict) {
            onConflict(`Approval #${approval.id}: ${error.detail} The list has been refreshed.`);
          }
        },
      },
    );
    setConfirming(null);
  };

  return (
    <li className="rounded-xl border border-warning/35 bg-warning-soft/60 p-5 shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-warning-strong">
            <PauseCircle aria-hidden className="size-4" /> Waiting for a decision · approval #{approval.id}
          </div>
          <h2 className="mt-1 text-lg font-semibold text-navy">{describeAction(approval)}</h2>
          <p className="text-2xl font-semibold text-navy tabular-nums">{usd(approval.refund_amount, 2)}</p>
        </div>
        <StatusBadge style={{ label: `${humanize(approval.risk_level)} risk`, tone: "tan", icon: PauseCircle }} />
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg bg-surface/80 p-4">
          <KeyValues
            items={[
              { label: "Customer", value: `#${approval.customer_id}` },
              { label: "Order", value: approval.order_id ?? "—" },
              { label: "Customer's reason", value: approval.customer_reason ?? "—" },
              { label: "Requested", value: dateTime(approval.created_at) },
              { label: "Tool", value: approval.tool_name, mono: true },
              { label: "Conversation", value: <Link className="text-blue-strong hover:underline" to={`/traces/${approval.execution_id}`}>{shortId(approval.execution_id)}</Link> },
            ]}
          />
          <p className="mt-4 text-xs text-text-muted">Why it needs review</p>
          <p className="text-sm text-navy">{approval.reason}</p>
        </div>

        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => e.preventDefault()}
          aria-label={`Decide approval ${approval.id}`}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={reviewerId}>Reviewer</Label>
            <Input id={reviewerId} value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="name@company.com" autoComplete="email" className="bg-surface" required />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={noteId}>Note <span className="font-normal text-text-muted">(optional, the agent may pass it on)</span></Label>
            <Textarea id={noteId} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} rows={3} className="bg-surface" />
          </div>
          {decide.isError && (
            <p role="alert" className="rounded-md border border-critical/25 bg-critical-soft px-3 py-2 text-sm text-critical-strong">
              {errorMessage(decide.error)}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button type="button" disabled={!ready} onClick={() => setConfirming("approve")} className="bg-success-strong text-white hover:bg-success-strong/90">
              {decide.isPending && decide.variables?.decision === "approve" ? "Approving…" : "Approve"}
            </Button>
            <Button type="button" variant="outline" disabled={!ready} onClick={() => setConfirming("reject")} className="border-critical/40 bg-surface text-critical-strong hover:bg-critical-soft">
              {decide.isPending && decide.variables?.decision === "reject" ? "Rejecting…" : "Reject"}
            </Button>
          </div>
          {!reviewer.trim() && <p className="text-xs text-text-muted">Enter your name to decide.</p>}
        </form>
      </div>

      <Dialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirming === "approve" ? "Approve this refund?" : "Reject this refund?"}</DialogTitle>
            <DialogDescription>
              {confirming === "approve"
                ? `${describeAction(approval)} and refund ${usd(approval.refund_amount, 2)}. The agent runs the action once, then replies to the customer.`
                : `The return won't be started. The agent tells the customer it was declined${note.trim() ? ", with your note" : ""}.`}{" "}
              This can't be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirming(null)}>Cancel</Button>
            <Button
              onClick={submit}
              className={confirming === "approve" ? "bg-success-strong text-white hover:bg-success-strong/90" : "bg-critical-strong text-white hover:bg-critical-strong/90"}
            >
              {confirming === "approve" ? "Approve and resume" : "Reject and resume"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </li>
  );
}

function DecidedCard({ approval }: { approval: Approval }) {
  const approved = approval.status === "approved";
  return (
    <li className={cn("rounded-xl border bg-surface p-4 shadow-card", approved ? "border-success/30" : "border-critical/30")}>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge style={approvalStyle(approval.status)} />
        <span className="font-medium text-navy">{describeAction(approval)}</span>
        <span className="text-sm text-navy tabular-nums">{usd(approval.refund_amount, 2)}</span>
        <span className="ml-auto text-xs text-text-muted">#{approval.id} · {dateTime(approval.decided_at)}</span>
      </div>
      <KeyValues
        className="mt-3 lg:grid-cols-4"
        items={[
          { label: "Decided by", value: approval.decided_by ?? "—" },
          { label: "Note", value: approval.decision_note ?? "—" },
          { label: "Action", value: humanize(approval.action_status) },
          {
            label: "Resumed execution",
            value: approval.resumed_at ? (
              <Link className="text-blue-strong hover:underline" to={`/traces/${approval.execution_id}`}>
                <Mono>{shortId(approval.execution_id)}</Mono> · {dateTime(approval.resumed_at)}
              </Link>
            ) : (
              "not resumed yet"
            ),
          },
        ]}
      />
    </li>
  );
}

export function Approvals() {
  const all = useApprovals();
  const [outcomes, setOutcomes] = useState<DecisionResponse[]>([]);
  const [conflicts, setConflicts] = useState<string[]>([]);
  return (
    <>
      <PageHeader
        title="Human approvals"
        description="Refunds over $500 pause the conversation until someone decides. Deciding resumes the same execution; an approved action runs exactly once."
      />
      {(outcomes.length > 0 || conflicts.length > 0) && (
        <div className="mb-4 flex flex-col gap-3">
          {conflicts.map((c) => (
            <p key={c} role="alert" className="rounded-lg border border-warning/35 bg-warning-soft px-4 py-3 text-sm text-warning-strong">{c}</p>
          ))}
          {outcomes.map((o) => <Outcome key={o.approval.id} result={o} />)}
        </div>
      )}
      <QueryState query={all} loadingRows={3}>
        {(approvals) => {
          const pending = approvals.filter((a) => a.status === "pending");
          const decided = approvals.filter((a) => a.status !== "pending");
          return (
            <Tabs defaultValue="pending">
              <TabsList>
                <TabsTrigger value="pending">Pending ({pending.length})</TabsTrigger>
                <TabsTrigger value="decided">Decided ({decided.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="pending" className="mt-4">
                {pending.length === 0 ? (
                  <Panel><EmptyState message="No approvals waiting." hint="A request appears here when the agent pauses a high-value refund." /></Panel>
                ) : (
                  <ul className="flex flex-col gap-4">{pending.map((a) => (
                      <PendingCard
                        key={a.id}
                        approval={a}
                        onDecided={(r) => setOutcomes((prev) => [r, ...prev])}
                        onConflict={(m) => setConflicts((prev) => [m, ...prev])}
                      />
                    ))}</ul>
                )}
              </TabsContent>
              <TabsContent value="decided" className="mt-4">
                {decided.length === 0 ? (
                  <Panel><EmptyState message="No decisions yet." /></Panel>
                ) : (
                  <ul className="flex flex-col gap-3">{decided.map((a) => <DecidedCard key={a.id} approval={a} />)}</ul>
                )}
              </TabsContent>
            </Tabs>
          );
        }}
      </QueryState>
    </>
  );
}
