import type { RunMetrics, TestRunListItem } from "@/api/types";

export type CompletedRun = TestRunListItem & { metrics: RunMetrics };

/** Completed, non-rerun runs that have metrics, oldest first. */
export function completedRuns(runs: TestRunListItem[]): CompletedRun[] {
  return runs
    .filter((r): r is CompletedRun => r.status === "completed" && r.metrics !== null && r.rerun_id === null)
    .sort((a, b) => a.id - b.id);
}

/**
 * The run a KPI trend may honestly compare against: the previous completed run of the
 * same agent version, over the same number of variants, graded by the same known check
 * set. Otherwise there's no trend: a change could come from the rules, not the agent.
 */
export function comparableEarlierRun(
  runs: CompletedRun[],
  latest: CompletedRun,
): CompletedRun | undefined {
  return [...runs]
    .reverse()
    .find(
      (r) =>
        r.id < latest.id &&
        r.agent_version === latest.agent_version &&
        r.variant_count === latest.variant_count &&
        latest.check_set_version !== null &&
        r.check_set_version === latest.check_set_version,
    );
}
