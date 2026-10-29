import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  AgentVersion,
  Approval,
  DecisionResponse,
  FailedCheck,
  ReportDetail,
  ReportSummary,
  Rerun,
  ResultDetail,
  ResultSummary,
  ScenarioDetail,
  ScenarioSummary,
  TestRunDetail,
  TestRunListItem,
  ToolCallView,
  TraceDetail,
  TraceEvent,
  TraceSummary,
} from "./types";

// One hook per endpoint the dashboard reads. Keys start with the resource name so a
// mutation can invalidate everything under it.

export interface RunFilters {
  agent_version?: string;
  status?: string;
  include_reruns?: boolean;
  limit?: number;
}

export function useTestRuns(filters: RunFilters = {}) {
  return useQuery({
    queryKey: ["test-runs", filters],
    queryFn: () => api.get<TestRunListItem[]>("/test-runs", { limit: 100, ...filters }),
  });
}

export function useTestRun(id: number) {
  return useQuery({
    queryKey: ["test-runs", id],
    queryFn: () => api.get<TestRunDetail>(`/test-runs/${id}`),
  });
}

export function useRunResults(id: number) {
  return useQuery({
    queryKey: ["test-runs", id, "results"],
    queryFn: () => api.get<ResultSummary[]>(`/test-runs/${id}/results`),
  });
}

export function useRunFailedChecks(id: number) {
  return useQuery({
    queryKey: ["test-runs", id, "failed-checks"],
    queryFn: () => api.get<FailedCheck[]>(`/test-runs/${id}/failed-checks`),
  });
}

export function useResult(runId: number, variantId: string) {
  return useQuery({
    queryKey: ["test-runs", runId, "results", variantId],
    queryFn: () =>
      api.get<ResultDetail>(`/test-runs/${runId}/results/${encodeURIComponent(variantId)}`),
  });
}

export function useScenarios() {
  return useQuery({
    queryKey: ["scenarios"],
    queryFn: () => api.get<ScenarioSummary[]>("/scenarios"),
  });
}

export function useScenario(id: string) {
  return useQuery({
    queryKey: ["scenarios", id],
    queryFn: () => api.get<ScenarioDetail>(`/scenarios/${encodeURIComponent(id)}`),
  });
}

export interface TraceFilters {
  source?: string;
  status?: string;
  test_run_id?: number;
}

export function useTraces(filters: TraceFilters = {}) {
  return useQuery({
    queryKey: ["traces", filters],
    queryFn: () => api.get<TraceSummary[]>("/traces", { limit: 100, ...filters }),
  });
}

export function useTrace(executionId: string) {
  return useQuery({
    queryKey: ["traces", executionId],
    queryFn: () => api.get<TraceDetail>(`/traces/${executionId}`),
  });
}

export function useTraceEvents(executionId: string) {
  return useQuery({
    queryKey: ["traces", executionId, "events"],
    queryFn: () => api.get<TraceEvent[]>(`/traces/${executionId}/events`),
  });
}

export function useTraceToolCalls(executionId: string) {
  return useQuery({
    queryKey: ["traces", executionId, "tool-calls"],
    queryFn: () => api.get<ToolCallView[]>(`/traces/${executionId}/tool-calls`),
  });
}

export function useComparisons() {
  return useQuery({
    queryKey: ["comparisons"],
    queryFn: () => api.get<ReportSummary[]>("/comparisons", { limit: 100 }),
  });
}

export function useComparison(id: number) {
  return useQuery({
    queryKey: ["comparisons", id],
    queryFn: () => api.get<ReportDetail>(`/comparisons/${id}`),
  });
}

export function useReruns(reportId: number) {
  return useQuery({
    queryKey: ["comparisons", reportId, "reruns"],
    queryFn: () => api.get<Rerun[]>(`/comparisons/${reportId}/reruns`),
  });
}

export function useApprovals(status?: Approval["status"]) {
  return useQuery({
    queryKey: ["approvals", status ?? "all"],
    queryFn: () => api.get<Approval[]>("/approvals", { status, limit: 200 }),
  });
}

export function useAgentVersions() {
  return useQuery({
    queryKey: ["agent-versions"],
    queryFn: () => api.get<AgentVersion[]>("/agent-versions"),
  });
}

export interface Decision {
  id: number;
  decision: "approve" | "reject";
  reviewer: string;
  note?: string;
}

/** Approving or rejecting resumes the paused execution, so traces change too. */
export function useDecideApproval() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision, reviewer, note }: Decision) =>
      api.post<DecisionResponse>(`/approvals/${id}/${decision}`, {
        reviewer,
        note: note?.trim() ? note.trim() : null,
      }),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["approvals"] });
      void client.invalidateQueries({ queryKey: ["traces"] });
    },
  });
}
