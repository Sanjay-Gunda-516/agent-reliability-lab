import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { approval } from "@/test/fixtures";
import { renderAt } from "@/test/render";
import { get } from "@/test/server";

describe("App shell", () => {
  it("has labelled navigation with every section", async () => {
    get("/scenarios", []);

    renderAt("/scenarios");

    const nav = (await screen.findAllByRole("navigation", { name: "Main" }))[0] as HTMLElement;
    const names = within(nav).getAllByRole("link").map((l) => l.textContent);
    expect(names).toEqual([
      "Overview",
      "Test Runs",
      "Scenarios",
      "Traces",
      "Regression",
      "Human Approvals",
      "Agent Versions",
    ]);
    expect(within(nav).getByRole("link", { name: "Scenarios" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("shows how many approvals are waiting", async () => {
    get("/approvals", [approval(1), approval(2)]);
    get("/scenarios", []);

    renderAt("/scenarios");

    expect(await screen.findByLabelText("2 pending")).toBeInTheDocument();
  });

  it("opens the navigation from the menu button on small screens", async () => {
    get("/scenarios", []);
    const { user } = renderAt("/scenarios");

    await user.click(await screen.findByRole("button", { name: "Open navigation" }));

    const dialog = await screen.findByRole("dialog", { name: "Navigation" });
    await user.click(within(dialog).getByRole("link", { name: "Traces" }));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument();
  });

  it("shows a friendly page for unknown addresses", async () => {
    renderAt("/nowhere");

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});
