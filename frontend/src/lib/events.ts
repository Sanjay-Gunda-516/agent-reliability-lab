import {
  Bot,
  ClipboardCheck,
  Compass,
  Gavel,
  Hand,
  Headset,
  MessageSquare,
  MousePointerClick,
  PauseCircle,
  PlayCircle,
  RotateCcw,
  Scale,
  Send,
  ShieldAlert,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import type { TraceEvent } from "@/api/types";

import type { Tone } from "./status";

export interface EventPresentation {
  title: string;
  icon: LucideIcon;
  tone: Tone;
  /** Short facts shown as chips under the title. */
  facts: string[];
  /** Selection events are shown compactly. */
  minor?: boolean;
}

const str = (v: unknown) => (v === null || v === undefined || v === "" ? null : String(v));

function tokenFact(e: TraceEvent): string | null {
  if (e.input_tokens === null && e.output_tokens === null) return null;
  const cached = (e.cache_read_input_tokens ?? 0) + (e.cache_creation_input_tokens ?? 0);
  return `${e.input_tokens ?? 0} in · ${e.output_tokens ?? 0} out${cached ? ` · ${cached} cached` : ""}`;
}

const compact = (items: (string | null | undefined)[]) => items.filter((x): x is string => Boolean(x));

/**
 * How each trace event type is shown. Normal flow is blue; warnings (retries, rule
 * rejections, pauses, escalations) are amber; errors red; successful completion green.
 */
export function presentEvent(e: TraceEvent): EventPresentation {
  const a = e.attributes;
  const failed = e.status === "error";
  switch (e.type) {
    case "request_received":
      return {
        title: "Customer message received",
        icon: MessageSquare,
        tone: "blue",
        facts: compact([str(a.message_chars) && `${String(a.message_chars)} characters`, str(a.history_messages) && `${String(a.history_messages)} earlier messages`]),
      };
    case "intent":
      return {
        title: "Intent classification",
        icon: Compass,
        tone: failed ? "critical" : "blue",
        facts: compact([str(a.intent), e.model, tokenFact(e)]),
      };
    case "model_call":
      return {
        title: "Claude call",
        icon: Bot,
        tone: failed ? "critical" : "blue",
        facts: compact([e.model, tokenFact(e), str(a.stop_reason) && `stop: ${String(a.stop_reason)}`]),
      };
    case "tool_selected":
      return { title: `Chose ${e.tool_name ?? "a tool"}`, icon: MousePointerClick, tone: "blue", facts: [], minor: true };
    case "tool_call":
      return {
        title: `Tool call · ${e.tool_name ?? "unknown"}`,
        icon: Wrench,
        tone: failed ? "critical" : e.status === "rejected" ? "warning" : "blue",
        facts: compact([e.attempt && e.attempt > 1 ? `${e.attempt} attempts` : null, e.error_type]),
      };
    case "tool_retry":
      return {
        title: `Retry · ${e.tool_name ?? "tool"}`,
        icon: RotateCcw,
        tone: "warning",
        facts: compact([e.attempt ? `attempt ${e.attempt}` : null, e.error_type]),
      };
    case "rule_rejection":
      return {
        title: `Rule rejection · ${e.tool_name ?? "tool"}`,
        icon: ShieldAlert,
        tone: "warning",
        facts: compact([str(a.error), str(a.reason)]),
      };
    case "escalation":
      return { title: "Escalated to a person", icon: Headset, tone: "warning", facts: compact([str(a.reason)]) };
    case "approval_requested":
      return {
        title: "Approval requested",
        icon: Hand,
        tone: "tan",
        facts: compact([
          str(a.approval_id) && `approval #${String(a.approval_id)}`,
          str(a.risk_level) && `${String(a.risk_level)} risk`,
          str(a.refund_amount) && `$${String(a.refund_amount)}`,
        ]),
      };
    case "execution_paused":
      return {
        title: "Paused for human approval",
        icon: PauseCircle,
        tone: "warning",
        facts: compact([str(a.approval_id) && `approval #${String(a.approval_id)}`]),
      };
    case "approval_decision": {
      const approved = a.decision === "approved";
      return {
        title: approved ? "Approved by reviewer" : "Rejected by reviewer",
        icon: Gavel,
        tone: approved ? "success" : "critical",
        facts: compact([str(a.decided_by)]),
      };
    }
    case "execution_resumed":
      return {
        title: "Execution resumed",
        icon: PlayCircle,
        tone: "blue",
        facts: compact([str(a.approval_id) && `approval #${String(a.approval_id)}`]),
      };
    case "response_finalized": {
      const status = str(a.status) ?? "";
      return {
        title: "Final response",
        icon: Send,
        tone: status === "completed" ? "success" : status ? "warning" : "blue",
        facts: compact([status.replace(/_/g, " "), str(a.reply_chars) && `${String(a.reply_chars)} characters`]),
      };
    }
    case "evaluation":
      return {
        title: "Evaluation",
        icon: ClipboardCheck,
        tone: a.verdict === "pass" ? "success" : a.verdict ? "critical" : "blue",
        facts: compact([str(a.verdict), Number(a.critical_failures) ? `${String(a.critical_failures)} critical` : null, Number(a.major_failures) ? `${String(a.major_failures)} major` : null]),
      };
    case "judge":
      return { title: "LLM judge (reported only)", icon: Scale, tone: "neutral", facts: compact([e.model, str(a.behavior), tokenFact(e)]) };
    default:
      return { title: e.type.replace(/_/g, " "), icon: MessageSquare, tone: failed ? "critical" : "neutral", facts: [] };
  }
}

export const WARNING_EVENTS = new Set([
  "tool_retry",
  "rule_rejection",
  "escalation",
  "execution_paused",
  "approval_requested",
]);

export function isNoteworthy(e: TraceEvent): boolean {
  return e.status !== "ok" || WARNING_EVENTS.has(e.type) || e.type === "approval_decision";
}
