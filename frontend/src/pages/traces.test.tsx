import { screen, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { event, traceDetail, traceSummary } from "@/test/fixtures";
import { renderAt } from "@/test/render";
import { API, get, server } from "@/test/server";

const ID = "1ed4c90fa06b4aaf863bac6aaabd71d8";

const approvalFlow = () => [
  event("request_received", { attributes: { message_chars: 72, history_messages: 0 } }),
  event("intent", { duration_ms: 2366, model: "claude-opus-5", input_tokens: 620, output_tokens: 22, attributes: { intent: "return_request" } }),
  event("model_call", { duration_ms: 1572, model: "claude-opus-5", input_tokens: 2, output_tokens: 118, cache_creation_input_tokens: 1798, attributes: { stop_reason: "tool_use" } }),
  event("tool_call", { tool_name: "get_order", duration_ms: 17 }),
  event("tool_call", { tool_name: "create_return", status: "rejected", error_type: "approval_required", duration_ms: 19 }),
  event("rule_rejection", { tool_name: "create_return", status: "rejected", attributes: { error: "approval_required" } }),
  event("approval_requested", { attributes: { approval_id: 2, risk_level: "high", refund_amount: "1249.00" } }),
  event("execution_paused", { attributes: { approval_id: 2 } }),
  event("response_finalized", { attributes: { status: "pending_approval", reply_chars: 166 } }),
  event("execution_resumed", { attributes: { approval_id: 2 } }),
  event("approval_decision", { attributes: { decision: "approved", decided_by: "live-check@support.example.com" } }),
  event("tool_call", { tool_name: "create_return", duration_ms: 6 }),
  event("tool_retry", { tool_name: "get_order", status: "retry", attempt: 2, error_type: "TimeoutError" }),
  event("model_call", { status: "error", error_type: "overloaded", error_message: "Claude is unavailable" }),
  event("response_finalized", { attributes: { status: "completed", reply_chars: 377 } }),
];

function serveTrace(events = approvalFlow()) {
  get(`/traces/${ID}`, traceDetail(ID));
  get(`/traces/${ID}/events`, events);
  get(`/traces/${ID}/tool-calls`, [
    { sequence: 1, turn: 1, tool_use_id: "t1", name: "create_return", arguments: { order_id: 7420 }, status: "rejected", error: "approval_required", attempts: 1, latency_ms: 19, output: null, started_at: null },
  ]);
}

function item(type: string, index = 0): HTMLElement {
  const items = document.querySelectorAll<HTMLElement>(`li[data-event-type="${type}"]`);
  const found = items[index];
  if (!found) throw new Error(`no ${type} event #${index}`);
  return found;
}

describe("Trace timeline", () => {
  it("renders each step with its name, timing, facts and status", async () => {
    serveTrace();

    renderAt(`/traces/${ID}`);

    const timeline = await screen.findByRole("list", { name: "Execution timeline" });
    expect(within(timeline).getByText("Intent classification")).toBeInTheDocument();
    expect(within(item("intent")).getByText("2.37 s")).toBeInTheDocument();
    expect(within(item("intent")).getByText("return_request")).toBeInTheDocument();
    expect(within(item("model_call")).getByText("2 in · 118 out · 1798 cached")).toBeInTheDocument();
    expect(within(item("tool_call", 1)).getByText("Tool call · create_return")).toBeInTheDocument();
    expect(within(item("tool_call", 1)).getByText("Rejected")).toBeInTheDocument();
    expect(within(item("rule_rejection")).getByText("approval_required")).toBeInTheDocument();
    expect(within(item("approval_requested")).getByText("$1249.00")).toBeInTheDocument();
    expect(within(item("execution_paused")).getByText("Paused for human approval")).toBeInTheDocument();
    expect(within(item("approval_decision")).getByText("Approved by reviewer")).toBeInTheDocument();
    expect(within(item("tool_retry")).getByText("Retry")).toBeInTheDocument();
    expect(within(item("model_call", 1)).getByText("overloaded: Claude is unavailable")).toBeInTheDocument();
    expect(within(item("model_call", 1)).getByText("Error")).toBeInTheDocument();
  });

  it("marks where a later request resumed the execution", async () => {
    serveTrace();

    renderAt(`/traces/${ID}`);

    const timeline = await screen.findByRole("list", { name: "Execution timeline" });
    const steps = within(timeline).getAllByRole("listitem").map((li) => li.textContent ?? "");
    const divider = steps.findIndex((t) => t.includes("Later request"));
    const resumed = steps.findIndex((t) => t.includes("Execution resumed"));
    expect(divider).toBeGreaterThan(steps.findIndex((t) => t.includes("Paused for human approval")));
    expect(resumed).toBe(divider + 1);
  });

  it("uses tones for normal flow, warnings, errors and completion", async () => {
    serveTrace();

    renderAt(`/traces/${ID}`);

    await screen.findByRole("list", { name: "Execution timeline" });
    const icon = (li: HTMLElement) => li.querySelector("span") as HTMLElement;
    expect(icon(item("model_call"))).toHaveClass("bg-blue-soft");
    expect(icon(item("rule_rejection"))).toHaveClass("bg-warning-soft");
    expect(icon(item("model_call", 1))).toHaveClass("bg-critical-soft");
    expect(icon(item("response_finalized", 1))).toHaveClass("bg-success-soft");
    expect(icon(item("response_finalized", 0))).toHaveClass("bg-warning-soft");
  });

  it("can show only warnings and errors", async () => {
    serveTrace();
    const { user } = renderAt(`/traces/${ID}`);
    await screen.findByRole("list", { name: "Execution timeline" });

    await user.click(screen.getByRole("checkbox", { name: "Only warnings and errors" }));

    expect(screen.queryByText("Intent classification")).not.toBeInTheDocument();
    expect(screen.getByText("Paused for human approval")).toBeInTheDocument();
    expect(screen.getByText("overloaded: Claude is unavailable")).toBeInTheDocument();
  });

  it("shows correlation ids, keeps raw data collapsed", async () => {
    serveTrace();

    renderAt(`/traces/${ID}`);

    expect(await screen.findByText(ID)).toBeInTheDocument();
    expect(screen.getByText("575765c1b5974311a06713e46cda9ee3")).toBeInTheDocument();
    const raw = (await screen.findByText("All events")).closest("details") as HTMLDetailsElement;
    expect(raw.open).toBe(false);
  });

  it("handles a trace with no events", async () => {
    serveTrace([]);

    renderAt(`/traces/${ID}`);

    expect(await screen.findByText("No events recorded.")).toBeInTheDocument();
  });
});

describe("Trace list", () => {
  it("filters by source through the API and searches locally", async () => {
    server.use(
      http.get(`${API}/traces`, ({ request }) => {
        const source = new URL(request.url).searchParams.get("source");
        const all = [
          traceSummary(ID),
          traceSummary("aaaa0000bbbb1111cccc2222dddd3333", { source: "evaluation", variant_id: "cancel-eligible-order:normal", test_run_id: 5 }),
        ];
        return HttpResponse.json(source ? all.filter((t) => t.source === source) : all);
      }),
    );
    const { user } = renderAt("/traces");
    expect(await screen.findByRole("link", { name: "1ed4c90f" })).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "Search traces" }), "cancel-eligible");
    expect(screen.queryByRole("link", { name: "1ed4c90f" })).not.toBeInTheDocument();
    expect(screen.getByText("run #5")).toBeInTheDocument();

    await user.clear(screen.getByRole("searchbox", { name: "Search traces" }));
    await user.click(screen.getByRole("combobox", { name: "Source" }));
    await user.click(await screen.findByRole("option", { name: "Live chat (API)" }));
    expect(await screen.findByRole("link", { name: "1ed4c90f" })).toBeInTheDocument();
    expect(screen.queryByText("run #5")).not.toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    get("/traces", []);

    renderAt("/traces");

    expect(await screen.findByText("No traces yet.")).toBeInTheDocument();
  });
});
