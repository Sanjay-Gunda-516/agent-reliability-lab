import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import type { Approval } from "@/api/types";
import { approval, reportSummary, run, version } from "@/test/fixtures";
import { renderAt } from "@/test/render";
import { API, get, server } from "@/test/server";

/** Serves the approval list, and moves it out of "pending" once decided. */
function serveApprovals(initial: Approval[], onDecision?: (path: string, body: unknown) => Response | undefined) {
  let current = initial;
  const bodies: { path: string; body: unknown }[] = [];
  server.use(
    http.get(`${API}/approvals`, ({ request }) => {
      const status = new URL(request.url).searchParams.get("status");
      return HttpResponse.json(status ? current.filter((a) => a.status === status) : current);
    }),
    http.post(`${API}/approvals/:id/:decision`, async ({ request, params }) => {
      const body = await request.json();
      bodies.push({ path: `${String(params.id)}/${String(params.decision)}`, body });
      const custom = onDecision?.(`${String(params.id)}/${String(params.decision)}`, body);
      if (custom) return custom;
      const approved = params.decision === "approve";
      const decided = approval(Number(params.id), {
        status: approved ? "approved" : "rejected",
        decided_by: (body as { reviewer: string }).reviewer,
        decision_note: (body as { note: string | null }).note,
        decided_at: "2026-10-29T02:00:00Z",
        action_status: approved ? "succeeded" : "not_run",
        resumed_at: "2026-10-29T02:00:03Z",
      });
      current = current.map((a) => (a.id === decided.id ? decided : a));
      return HttpResponse.json({
        approval: decided,
        execution_id: decided.execution_id,
        execution_status: "completed",
        final_response: approved ? "Your return for order #7420 is approved." : "Sorry, the refund was declined.",
      });
    }),
  );
  return bodies;
}

describe("Human approvals", () => {
  it("shows what's being asked and why", async () => {
    serveApprovals([approval(2)]);

    renderAt("/approvals");

    expect(await screen.findByRole("heading", { name: "Start a return for order 7420" })).toBeInTheDocument();
    expect(screen.getByText("$1249.00")).toBeInTheDocument();
    expect(screen.getByText("High risk")).toBeInTheDocument();
    expect(screen.getByText("Refund of $1249.00 is over the $500.00 limit for automatic refunds.")).toBeInTheDocument();
    expect(screen.getByText("Screen flickers constantly")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Pending (1)" })).toBeInTheDocument();
  });

  it("needs a reviewer before deciding", async () => {
    serveApprovals([approval(2)]);

    const { user } = renderAt("/approvals");
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();

    await user.type(screen.getByLabelText("Reviewer"), "sam@support.example.com");

    expect(approve).toBeEnabled();
  });

  it("approves only after confirmation, then shows the resumed outcome", async () => {
    const bodies = serveApprovals([approval(2)]);
    const { user } = renderAt("/approvals");
    await user.type(await screen.findByLabelText("Reviewer"), "sam@support.example.com");
    await user.type(screen.getByLabelText(/Note/), "Confirmed from photos");

    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog", { name: "Approve this refund?" });
    expect(within(dialog).getByText(/runs the action once/)).toBeInTheDocument();
    expect(bodies).toHaveLength(0); // nothing sent until confirmed
    await user.click(within(dialog).getByRole("button", { name: "Approve and resume" }));

    expect(await screen.findByText("Your return for order #7420 is approved.")).toBeInTheDocument();
    expect(bodies).toEqual([
      { path: "2/approve", body: { reviewer: "sam@support.example.com", note: "Confirmed from photos" } },
    ]);
    expect(screen.getByRole("link", { name: /View the resumed execution/ })).toHaveAttribute(
      "href",
      "/traces/1ed4c90fa06b4aaf863bac6aaabd71d8",
    );
    expect(await screen.findByRole("tab", { name: "Pending (0)" })).toBeInTheDocument();
  });

  it("cancelling the confirmation sends nothing", async () => {
    const bodies = serveApprovals([approval(2)]);
    const { user } = renderAt("/approvals");
    await user.type(await screen.findByLabelText("Reviewer"), "sam");

    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));

    expect(bodies).toHaveLength(0);
    expect(screen.getByRole("heading", { name: "Start a return for order 7420" })).toBeInTheDocument();
  });

  it("rejects with an empty note sent as null", async () => {
    const bodies = serveApprovals([approval(2)]);
    const { user } = renderAt("/approvals");
    await user.type(await screen.findByLabelText("Reviewer"), "sam");

    await user.click(screen.getByRole("button", { name: "Reject" }));
    await user.click(within(await screen.findByRole("dialog", { name: "Reject this refund?" })).getByRole("button", { name: "Reject and resume" }));

    expect(await screen.findByText("Sorry, the refund was declined.")).toBeInTheDocument();
    expect(bodies[0]).toEqual({ path: "2/reject", body: { reviewer: "sam", note: null } });
  });

  it("explains a conflict when someone else decided first", async () => {
    serveApprovals([approval(2)], () =>
      HttpResponse.json({ detail: "Approval 2 was already approved by alex@support.example.com." }, { status: 409 }),
    );
    const { user } = renderAt("/approvals");
    await user.type(await screen.findByLabelText("Reviewer"), "sam");

    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve and resume" }));

    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => a.textContent?.includes("already approved by alex@support.example.com"))).toBe(true);
  });

  it("keeps other errors on the card so the reviewer can retry", async () => {
    serveApprovals([approval(2)], () => HttpResponse.json({ detail: "Agent is not configured." }, { status: 503 }));
    const { user } = renderAt("/approvals");
    await user.type(await screen.findByLabelText("Reviewer"), "sam");

    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve and resume" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Agent is not configured.");
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("remembers the reviewer name for next time", async () => {
    serveApprovals([approval(2), approval(3)]);
    const { user } = renderAt("/approvals");
    const [first] = await screen.findAllByLabelText("Reviewer");
    await user.type(first as HTMLElement, "sam");

    await user.click(screen.getAllByRole("button", { name: "Approve" })[0] as HTMLElement);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Approve and resume" }));
    await screen.findByText("Your return for order #7420 is approved.");

    expect(localStorage.getItem("arl.reviewer")).toBe("sam");
  });

  it("lists decisions with reviewer, outcome and the resumed execution", async () => {
    serveApprovals([
      approval(2, { status: "approved", decided_by: "live-check@support.example.com", decided_at: "2026-10-29T02:00:00Z", action_status: "succeeded", resumed_at: "2026-10-29T02:00:03Z" }),
      approval(3, { status: "rejected", decided_by: "sam", decision_note: "Outside warranty", decided_at: "2026-10-29T03:00:00Z" }),
    ]);
    const { user } = renderAt("/approvals");

    await user.click(await screen.findByRole("tab", { name: "Decided (2)" }));

    expect(await screen.findByText("live-check@support.example.com")).toBeInTheDocument();
    expect(screen.getByText("Succeeded")).toBeInTheDocument();
    expect(screen.getByText("Outside warranty")).toBeInTheDocument();
    expect(screen.getByText("Approved").closest("li")).toHaveClass("border-success/30");
    expect(screen.getByText("Rejected").closest("li")).toHaveClass("border-critical/30");
    expect(screen.getByText("not resumed yet")).toBeInTheDocument();
  });

  it("shows an empty pending list", async () => {
    serveApprovals([]);

    renderAt("/approvals");

    expect(await screen.findByText("No approvals waiting.")).toBeInTheDocument();
  });
});

describe("Agent versions", () => {
  it("shows versions with their runs, comparisons and configuration differences", async () => {
    get("/agent-versions", [
      version("default", { defined_in_code: false, description: "Recorded from test runs made before agent versions existed." }),
      version("v1"),
      version("v1-regressed"),
    ]);
    get("/test-runs", [run(5, { agent_version: "v1-regressed" }), run(4)]);
    get("/comparisons", [reportSummary(3)]);

    renderAt("/versions");

    const v1 = (await screen.findByRole("heading", { name: "v1" })).closest("li") as HTMLElement;
    expect(within(v1).getByText("Defined in code")).toBeInTheDocument();
    expect(within(v1).getByRole("link", { name: "Run #4" })).toHaveAttribute("href", "/runs/4");
    expect(within(v1).getByText("as baseline")).toBeInTheDocument();
    const legacy = screen.getByRole("heading", { name: "default" }).closest("li") as HTMLElement;
    expect(within(legacy).getByText("Known only from old runs")).toBeInTheDocument();
    expect(within(legacy).getByText("No runs yet.")).toBeInTheDocument();

    expect(screen.getByText("1 setting differ")).toBeInTheDocument();
    const promptRow = screen.getByText("differs").closest("tr") as HTMLElement;
    expect(within(promptRow).getByText("Prompt version")).toBeInTheDocument();
  });
});
