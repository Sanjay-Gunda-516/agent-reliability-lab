import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { comparisonVariant, reportDetail, reportSummary } from "@/test/fixtures";
import { renderAt } from "@/test/render";
import { get } from "@/test/server";

const CONFIRMED = comparisonVariant("cancel-eligible-order:normal", {
  category: "confirmed_regression",
  baseline_verdict: "pass",
  candidate_verdict: "fail",
  new_failed_checks: ["tools.lookup_before_action[cancel_order(order_id=4821)]"],
  explanation: "pass → fail. Candidate reruns repeated the regression in 3/3; baseline reruns showed the same change in 0/3, so it holds up.",
  rerun_evidence: {
    first_category: "regression",
    outcome: "confirmed_regression",
    candidate: [6, 7, 8].map((id) => ({ test_run_id: id, result_id: id + 17, verdict: "fail" as const, critical_failures: [], scenario_hash: "h", counted: true, reproduces: true })),
    baseline: [9, 10, 11].map((id) => ({ test_run_id: id, result_id: id + 17, verdict: "pass" as const, critical_failures: [], scenario_hash: "h", counted: true, reproduces: false })),
    candidate_reproduced: 3,
    candidate_counted: 3,
    baseline_reproduced: 0,
    baseline_counted: 3,
    explanation: "Candidate reruns repeated the regression in 3/3; baseline reruns showed the same change in 0/3, so it holds up.",
  },
});

function serveReport(variants = [CONFIRMED, comparisonVariant("refund-eligibility-question:normal")]) {
  get("/comparisons/3", reportDetail(3, { revision: 2, root_report_id: 2, revisions: [2, 3], variants, counts: { confirmed_regression: 1, unchanged: 1 } }));
  get("/comparisons/2/reruns", [
    { id: 1, report_id: 2, repetitions: 3, sides: "both", variant_ids: ["cancel-eligible-order:normal"], status: "completed", error: null, result_report_id: 3, created_at: "2026-10-29T04:00:00Z", finished_at: "2026-10-29T04:05:00Z", test_run_ids: [6, 7, 8, 9, 10, 11], estimated_cost_usd: null },
  ]);
}

describe("Regression report", () => {
  it("puts baseline and candidate side by side with the pass-rate delta", async () => {
    serveReport();

    renderAt("/regression/3");

    expect(await screen.findByRole("heading", { name: "v1-regressed vs v1" })).toBeInTheDocument();
    expect(screen.getAllByText("Gate failed").length).toBeGreaterThan(0);
    expect(screen.getAllByText("−25 pts").length).toBeGreaterThan(0);
    expect(screen.getByText("Task success fell 25.00 points (3/4 → 2/4), more than the 5-point limit.")).toBeInTheDocument();
    expect(screen.getByText("Run #4").closest("a")).toHaveAttribute("href", "/runs/4");
  });

  it("shows a confirmed regression with its failing check and rerun evidence", async () => {
    serveReport();

    renderAt("/regression/3");

    const card = (await screen.findByText("cancel-eligible-order")).closest("li") as HTMLElement;
    expect(within(card).getByText("Confirmed regression")).toBeInTheDocument();
    expect(within(card).getByText("Lookup before action")).toBeInTheDocument();
    expect(within(card).getByText("cancel_order(order_id=4821)")).toBeInTheDocument();
    expect(within(card).getByText("3/3 regression")).toBeInTheDocument();
    expect(within(card).getByText("0/3 same change")).toBeInTheDocument();
    expect(within(card).getAllByText("fail")).toHaveLength(3); // screen-reader text per rerun
    expect(within(card).getByText(/after reruns: confirmed regression/)).toBeInTheDocument();
    // Ordered first, ahead of unchanged variants.
    const cards = screen.getAllByRole("listitem").filter((li) => li.querySelector("[class*='rounded-xl']") || li.className.includes("rounded-xl"));
    expect(cards[0]).toBe(card);
  });

  it("styles each category distinctly and labels it in words", async () => {
    serveReport([
      comparisonVariant("a:normal", { category: "regression", needs_rerun: true }),
      comparisonVariant("b:normal", { category: "noisy", gated: false }),
      comparisonVariant("c:normal", { category: "checks_changed", gated: false }),
      comparisonVariant("d:normal", { category: "improvement" }),
    ]);

    renderAt("/regression/3");

    const badge = async (label: string) => (await screen.findAllByText(label))[0]?.closest("span") as HTMLElement;
    expect(await badge("Provisional regression")).toHaveClass("bg-warning-soft");
    expect(await badge("Noisy")).toHaveClass("bg-warning-soft");
    expect(await badge("Checks changed")).toHaveClass("bg-neutral-soft");
    expect(await badge("Improvement")).toHaveClass("bg-success-soft");
    expect(screen.getByText(/Rerun it to tell a real regression from model noise/)).toBeInTheDocument();
    expect(screen.getAllByText(/not counted by the gate/)).toHaveLength(2);
  });

  it("filters variants by category", async () => {
    serveReport();
    const { user } = renderAt("/regression/3");
    await screen.findByText("cancel-eligible-order");

    await user.click(screen.getByRole("combobox", { name: "Category" }));
    await user.click(await screen.findByRole("option", { name: "Unchanged" }));

    expect(screen.queryByText("cancel-eligible-order")).not.toBeInTheDocument();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("links the revisions and reports the rerun", async () => {
    serveReport();

    renderAt("/regression/3");

    const nav = await screen.findByRole("navigation", { name: "Report revisions" });
    expect(within(nav).getByRole("link", { name: "Revision 2 · after rerun" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Revision 1 · original" })).toHaveAttribute("href", "/regression/2");
    expect(await within(nav).findByText("Rerun 1: 3× both sides · completed")).toBeInTheDocument();
  });

  it("colours metric deltas by whether they're better or worse", async () => {
    serveReport();

    renderAt("/regression/3");

    const cost = (await screen.findByText("Estimated cost")).closest("tr") as HTMLElement;
    expect(within(cost).getByText(/−\$0\.0132/)).toHaveClass("text-success-strong");
    const task = screen.getByText("Task success", { selector: "td" }).closest("tr") as HTMLElement;
    expect(within(task).getByText(/−25 pts/)).toHaveClass("text-critical-strong");
  });
});

describe("Regression list", () => {
  it("lists reports with gate status, counts and check sets", async () => {
    get("/comparisons", [reportSummary(3, { revision: 2, root_report_id: 2, counts: { confirmed_regression: 1, unchanged: 3 } }), reportSummary(4, { gate_status: "pass", baseline_check_set_versions: [null], candidate_check_set_versions: [null], counts: { checks_changed: 4 } })]);

    renderAt("/regression");

    const row3 = (await screen.findByRole("link", { name: "#3" })).closest("tr") as HTMLElement;
    expect(within(row3).getByText("revision 2 of #2")).toBeInTheDocument();
    expect(within(row3).getByText("1 confirmed regression")).toBeInTheDocument();
    expect(within(row3).getByText("Gate failed")).toBeInTheDocument();
    const row4 = screen.getByRole("link", { name: "#4" }).closest("tr") as HTMLElement;
    expect(within(row4).getByText("unknown · unknown")).toBeInTheDocument();
    expect(within(row4).getByText("4 checks changed")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    get("/comparisons", []);

    renderAt("/regression");

    expect(await screen.findByText("No regression reports available.")).toBeInTheDocument();
  });
});
