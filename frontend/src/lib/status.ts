import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  Clock,
  FileDiff,
  HelpCircle,
  Loader2,
  MinusCircle,
  PauseCircle,
  PlusCircle,
  RotateCcw,
  Shuffle,
  TrendingDown,
  TrendingUp,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import type { Category } from "@/api/types";

export type Tone = "blue" | "success" | "warning" | "critical" | "neutral" | "tan";

export interface StatusStyle {
  label: string;
  tone: Tone;
  icon: LucideIcon;
}

// Tone -> classes. The only place status colours are chosen.
export const TONE_CLASSES: Record<Tone, string> = {
  blue: "bg-blue-soft text-navy border-blue/25",
  success: "bg-success-soft text-success-strong border-success/30",
  warning: "bg-warning-soft text-warning-strong border-warning/35",
  critical: "bg-critical-soft text-critical-strong border-critical/30",
  neutral: "bg-neutral-soft text-neutral-strong border-neutral/30",
  tan: "bg-tan-soft text-tan-strong border-tan/35",
};

export const TONE_TEXT: Record<Tone, string> = {
  blue: "text-blue-strong",
  success: "text-success-strong",
  warning: "text-warning-strong",
  critical: "text-critical-strong",
  neutral: "text-neutral-strong",
  tan: "text-tan-strong",
};

const unknown = (value: string): StatusStyle => ({
  label: value,
  tone: "neutral",
  icon: HelpCircle,
});

const VERDICTS: Record<string, StatusStyle> = {
  pass: { label: "Pass", tone: "success", icon: CheckCircle2 },
  fail: { label: "Fail", tone: "critical", icon: XCircle },
  error: { label: "Error", tone: "critical", icon: AlertTriangle },
};

const RUN_STATUSES: Record<string, StatusStyle> = {
  completed: { label: "Completed", tone: "success", icon: CheckCircle2 },
  running: { label: "Running", tone: "blue", icon: Loader2 },
  queued: { label: "Queued", tone: "neutral", icon: Clock },
  failed: { label: "Failed", tone: "critical", icon: XCircle },
};

const GATES: Record<string, StatusStyle> = {
  pass: { label: "Gate passed", tone: "success", icon: CheckCircle2 },
  warn: { label: "Gate warning", tone: "warning", icon: AlertTriangle },
  fail: { label: "Gate failed", tone: "critical", icon: XCircle },
};

export const CATEGORIES: Record<Category, StatusStyle> = {
  confirmed_regression: { label: "Confirmed regression", tone: "critical", icon: TrendingDown },
  regression: { label: "Provisional regression", tone: "warning", icon: TrendingDown },
  improvement: { label: "Improvement", tone: "success", icon: TrendingUp },
  unchanged: { label: "Unchanged", tone: "neutral", icon: MinusCircle },
  noisy: { label: "Noisy", tone: "warning", icon: Shuffle },
  scenario_changed: { label: "Scenario changed", tone: "neutral", icon: FileDiff },
  checks_changed: { label: "Checks changed", tone: "neutral", icon: FileDiff },
  missing_in_candidate: { label: "Missing in candidate", tone: "neutral", icon: CircleDashed },
  new_in_candidate: { label: "New in candidate", tone: "neutral", icon: PlusCircle },
};

const SEVERITIES: Record<string, StatusStyle> = {
  critical: { label: "Critical", tone: "critical", icon: XCircle },
  major: { label: "Major", tone: "warning", icon: AlertTriangle },
  minor: { label: "Minor", tone: "neutral", icon: CircleDot },
};

const APPROVALS: Record<string, StatusStyle> = {
  pending: { label: "Pending review", tone: "warning", icon: PauseCircle },
  approved: { label: "Approved", tone: "success", icon: CheckCircle2 },
  rejected: { label: "Rejected", tone: "critical", icon: Ban },
};

const EXECUTIONS: Record<string, StatusStyle> = {
  completed: { label: "Completed", tone: "success", icon: CheckCircle2 },
  escalated: { label: "Escalated", tone: "warning", icon: AlertTriangle },
  pending_approval: { label: "Awaiting approval", tone: "warning", icon: PauseCircle },
  error: { label: "Error", tone: "critical", icon: XCircle },
};

const EVENT_STATUSES: Record<string, StatusStyle> = {
  ok: { label: "OK", tone: "blue", icon: CheckCircle2 },
  rejected: { label: "Rejected", tone: "warning", icon: Ban },
  retry: { label: "Retry", tone: "warning", icon: RotateCcw },
  error: { label: "Error", tone: "critical", icon: XCircle },
};

export const verdictStyle = (v: string) => VERDICTS[v] ?? unknown(v);
export const runStatusStyle = (v: string) => RUN_STATUSES[v] ?? unknown(v);
export const gateStyle = (v: string) => GATES[v] ?? unknown(v);
export const categoryStyle = (v: string) => CATEGORIES[v as Category] ?? unknown(v);
export const severityStyle = (v: string) => SEVERITIES[v] ?? unknown(v);
export const approvalStyle = (v: string) => APPROVALS[v] ?? unknown(v);
export const executionStyle = (v: string) => EXECUTIONS[v] ?? unknown(v);
export const eventStatusStyle = (v: string) => EVENT_STATUSES[v] ?? unknown(v);
