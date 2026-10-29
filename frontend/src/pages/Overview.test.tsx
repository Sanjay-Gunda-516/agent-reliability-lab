import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { approval, metrics, reportSummary, run, version } from "@/test/fixtures";
import { renderAt } from "@/test/render";
import { emptyLists, get } from "@/test/server";

function common() {
  get("/agent-versions", [version("v1"), version("v1-regressed")]);
  get("/comparisons", [reportSummary(2)]);
  get("/approvals", [approval(7)]);
}

describe("Overview", () => {
  it("shows KPIs from the latest completed run", async () => {
    common();
    get("/test-runs", [run(5, { metrics: metrics(2, 4) }), run(4)]);

    renderAt("/");

    expect(await screen.findByText(/latest completed run, #5/)).toBeInTheDocument();
    const taskSuccess = screen.getByText("Task success").closest("div.rounded-xl") as HTMLElement;
    expect(within(taskSuccess).getByText("50%")).toBeInTheDocument();
    expect(within(taskSuccess).getByText("2/4")).toBeInTheDocument();
    // Run 4 has the same version and variant count, so a real trend exists: 75% -> 50%.
    expect(within(taskSuccess).getByText("▼ 25 pts")).toHaveAttribute(
      "title",
      "Compared with run #4 (same version, variant count and check set)",
    );
    expect(screen.getByText("$0.0914")).toBeInTheDocument();
    expect(screen.getByText(/checks checks-v2/)).toBeInTheDocument();
  });

  it.each([
    ["another agent version", { agent_version: "v1-regressed" }],
    ["another check set", { check_set_version: "checks-v1" }],
    ["an unknown check set", { check_set_version: null }],
  ])("shows no trend when the earlier run used %s", async (_, earlier) => {
    common();
    get("/test-runs", [run(5), run(4, earlier)]);

    renderAt("/");

    await screen.findByText(/latest completed run, #5/);
    expect(screen.queryByText(/pts$/)).not.toBeInTheDocument();
    expect(screen.queryByText("no change")).not.toBeInTheDocument();
  });

  it("lists recent reports and pending approvals", async () => {
    common();
    get("/test-runs", [run(4)]);

    renderAt("/");

    expect(await screen.findByText("v1-regressed vs v1")).toBeInTheDocument();
    expect(screen.getByText("Gate failed")).toBeInTheDocument();
    expect(await screen.findByText("create_return · order 7420")).toBeInTheDocument();
  });

  it("explains the empty state instead of inventing numbers", async () => {
    emptyLists("/test-runs", "/comparisons", "/approvals", "/agent-versions");

    renderAt("/");

    expect(await screen.findAllByText("No test runs yet.")).not.toHaveLength(0);
    expect(screen.getByText("No regression reports available.")).toBeInTheDocument();
    expect(screen.getByText("No approvals waiting.")).toBeInTheDocument();
    expect(screen.queryByText("%")).not.toBeInTheDocument();
  });

  it("shows a loading state, then an API error with a retry", async () => {
    common();
    get("/test-runs", { detail: "database unavailable" }, 503);

    renderAt("/");

    expect(await screen.findByRole("status", { name: "Loading runs" })).toBeInTheDocument();
    const alerts = await screen.findAllByRole("alert");
    expect(alerts[0]).toHaveTextContent("database unavailable");
    expect(screen.getAllByRole("button", { name: "Try again" })[0]).toBeEnabled();
  });
});
