import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { metrics, result, resultDetail, run, runDetail, scenario, version } from "@/test/fixtures";
import { renderAt } from "@/test/render";
import { API, get, server } from "@/test/server";

const LOOKUP_FAILURE = {
  name: "tools.lookup_before_action",
  target: "cancel_order(order_id=4821)",
  source: "deterministic" as const,
  severity: "major" as const,
  passed: false,
  reason: "cancel_order(order_id=4821) was attempted without ever looking up order 4821.",
  expected: "a lookup returning order 4821 before every cancel_order attempt",
  actual: { attempts: [{ call: 1, tool: "cancel_order" }], lookups: [] },
};

describe("Test runs list", () => {
  it("shows status, version, counts, cost and check set", async () => {
    get("/agent-versions", [version("v1")]);
    get("/test-runs", [
      run(5, { agent_version: "v1-regressed", metrics: metrics(2, 4) }),
      run(4, { check_set_version: null, status: "running", metrics: null }),
    ]);

    renderAt("/runs");

    const row5 = (await screen.findByRole("link", { name: "#5" })).closest("tr") as HTMLElement;
    expect(within(row5).getByText("v1-regressed")).toBeInTheDocument();
    expect(within(row5).getByText("Completed")).toBeInTheDocument();
    expect(within(row5).getByText("checks-v2")).toBeInTheDocument();
    expect(within(row5).getByText("$0.0914")).toBeInTheDocument();
    const row4 = screen.getByRole("link", { name: "#4" }).closest("tr") as HTMLElement;
    expect(within(row4).getByText("Running")).toBeInTheDocument();
    expect(within(row4).getByText("unknown")).toBeInTheDocument();
  });

  it("filters by agent version through the API", async () => {
    get("/agent-versions", [version("v1"), version("v1-regressed")]);
    server.use(
      http.get(`${API}/test-runs`, ({ request }) => {
        const v = new URL(request.url).searchParams.get("agent_version");
        return HttpResponse.json(v ? [run(5, { agent_version: v })] : [run(5), run(4)]);
      }),
    );
    const { user } = renderAt("/runs");
    await screen.findByRole("link", { name: "#4" });

    await user.click(screen.getByRole("combobox", { name: "Agent version" }));
    await user.click(await screen.findByRole("option", { name: "v1-regressed" }));

    expect(await screen.findByText("v1-regressed", { selector: "td" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "#4" })).not.toBeInTheDocument();
  });

  it("says when filters match nothing", async () => {
    get("/agent-versions", []);
    get("/test-runs", []);

    renderAt("/runs?status=failed");

    expect(await screen.findByText("No test runs match these filters.")).toBeInTheDocument();
  });
});

describe("Run detail", () => {
  function serve() {
    get("/test-runs/5", runDetail(5, { agent_version: "v1-regressed" }));
    get("/test-runs/5/results", [
      result("cancel-eligible-order:normal", { verdict: "fail", major_failures: 1 }),
      result("refund-eligibility-question:normal", { intent: "refund_inquiry" }),
    ]);
    get("/test-runs/5/failed-checks", [
      { ...LOOKUP_FAILURE, variant_id: "cancel-eligible-order:normal", scenario_id: "cancel-eligible-order" },
    ]);
    get("/scenarios", [scenario("cancel-eligible-order", { tags: ["happy-path", "policy"] })]);
  }

  it("lists scenario results with tags, verdicts and failure counts", async () => {
    serve();

    renderAt("/runs/5");

    const link = await screen.findByRole("link", { name: "cancel-eligible-order" });
    const row = link.closest("tr") as HTMLElement;
    expect(within(row).getByText("Fail")).toBeInTheDocument();
    expect(within(row).getByText("1 major")).toBeInTheDocument();
    expect(within(row).getByText("happy-path")).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/runs/5/results/cancel-eligible-order%3Anormal");
    expect(screen.getByText(/checks checks-v2/)).toBeInTheDocument();
  });

  it("filters results by verdict", async () => {
    serve();
    const { user } = renderAt("/runs/5");
    await screen.findByRole("link", { name: "cancel-eligible-order" });

    await user.click(screen.getByRole("combobox", { name: "Verdict" }));
    await user.click(await screen.findByRole("option", { name: "Pass" }));

    expect(screen.queryByRole("link", { name: "cancel-eligible-order" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "refund-eligibility-question" })).toBeInTheDocument();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("shows failed checks with their severity", async () => {
    serve();
    const { user } = renderAt("/runs/5");
    await screen.findByRole("link", { name: "cancel-eligible-order" });

    await user.click(screen.getByRole("tab", { name: "Failed checks" }));

    expect(await screen.findByText(LOOKUP_FAILURE.reason)).toBeInTheDocument();
    expect(screen.getByText("Major")).toBeInTheDocument();
    expect(screen.queryByText(/"attempts"/)).not.toBeVisible(); // raw data stays collapsed
  });

  it("shows a not-found error for an unknown run", async () => {
    get("/test-runs/99", { detail: "Test run not found." }, 404);
    get("/test-runs/99/results", []);
    get("/test-runs/99/failed-checks", []);
    get("/scenarios", []);

    renderAt("/runs/99");

    expect(await screen.findByRole("alert")).toHaveTextContent("Not found.");
  });
});

describe("Scenario result", () => {
  it("lines up expected and actual using the backend's checks", async () => {
    get("/test-runs/5/results/cancel-eligible-order:normal", resultDetail("cancel-eligible-order:normal", {
      verdict: "fail",
      checks: [
        { ...LOOKUP_FAILURE },
        { name: "status.final", target: null, source: "deterministic", severity: "critical", passed: true, reason: "Conversation ended completed, as expected.", expected: "completed", actual: "completed" },
      ],
    }));
    get("/scenarios/cancel-eligible-order", scenario("cancel-eligible-order"));
    get("/traces/1ed4c90fa06b4aaf863bac6aaabd71d8/tool-calls", [
      { sequence: 1, turn: 1, tool_use_id: "t1", name: "cancel_order", arguments: { order_id: 4821 }, status: "success", error: null, attempts: 1, latency_ms: 5, output: null, started_at: null },
    ]);

    renderAt("/runs/5/results/cancel-eligible-order:normal");

    expect(await screen.findByRole("heading", { name: "cancel-eligible-order" })).toBeInTheDocument();
    const lookupRow = (await screen.findByText("Lookup before action")).parentElement as HTMLElement;
    expect(within(lookupRow).getByText("acted before looking up")).toBeInTheDocument();
    expect(within(lookupRow).getByText("Differs")).toBeInTheDocument();
    const statusRow = screen.getByText("Final status").parentElement as HTMLElement;
    expect(within(statusRow).getByText("Matches")).toBeInTheDocument();
    const toolsRow = screen.getByText("Required tools").parentElement as HTMLElement;
    expect(await within(toolsRow).findByText("cancel_order(order_id=4821)", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText("Please cancel order #4821.")).toBeInTheDocument();
    expect(screen.getByText("checks-v2")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open the trace/ })).toHaveAttribute(
      "href",
      "/traces/1ed4c90fa06b4aaf863bac6aaabd71d8",
    );
  });

  it("warns when the scenario changed after the run", async () => {
    get("/test-runs/5/results/cancel-eligible-order:normal", resultDetail("cancel-eligible-order:normal", { scenario_hash: "0000aaaa" }));
    get("/scenarios/cancel-eligible-order", scenario("cancel-eligible-order"));
    get("/traces/1ed4c90fa06b4aaf863bac6aaabd71d8/tool-calls", []);

    renderAt("/runs/5/results/cancel-eligible-order:normal");

    expect(await screen.findByRole("note")).toHaveTextContent("This scenario has changed since the run");
  });
});

describe("Scenarios", () => {
  it("searches the catalogue", async () => {
    get("/scenarios", [
      scenario("cancel-eligible-order"),
      scenario("high-value-refund-needs-approval", { title: "Return of a $1,249 laptop", tags: ["escalation"] }),
    ]);
    const { user } = renderAt("/scenarios");
    await screen.findByRole("link", { name: "cancel-eligible-order" });

    await user.type(screen.getByRole("searchbox", { name: "Search scenarios" }), "laptop");

    expect(screen.queryByRole("link", { name: "cancel-eligible-order" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "high-value-refund-needs-approval" })).toBeInTheDocument();
  });

  it("shows a scenario's expectations", async () => {
    get("/scenarios/cancel-eligible-order", scenario("cancel-eligible-order"));

    renderAt("/scenarios/cancel-eligible-order");

    expect(await screen.findByText("Cancels order 4821 and confirms it.")).toBeInTheDocument();
    expect(screen.getByText("Order cancelled")).toBeInTheDocument();
  });
});
