import type { RouteObject } from "react-router";

import { LoadingState } from "@/components/common/QueryState";
import { AppShell } from "@/components/layout/AppShell";

// Each page is its own chunk, so the first load is just the shell (charts and page
// code arrive when a page is opened).
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    hydrateFallbackElement: <LoadingState rows={4} label="Loading page" />,
    children: [
      { index: true, lazy: async () => ({ Component: (await import("@/pages/Overview")).Overview }) },
      { path: "runs", lazy: async () => ({ Component: (await import("@/pages/TestRuns")).TestRuns }) },
      { path: "runs/:runId", lazy: async () => ({ Component: (await import("@/pages/RunDetail")).RunDetail }) },
      {
        path: "runs/:runId/results/:variantId",
        lazy: async () => ({ Component: (await import("@/pages/ResultDetail")).ResultDetail }),
      },
      { path: "scenarios", lazy: async () => ({ Component: (await import("@/pages/Scenarios")).Scenarios }) },
      {
        path: "scenarios/:scenarioId",
        lazy: async () => ({ Component: (await import("@/pages/Scenarios")).ScenarioPage }),
      },
      { path: "traces", lazy: async () => ({ Component: (await import("@/pages/Traces")).Traces }) },
      {
        path: "traces/:executionId",
        lazy: async () => ({ Component: (await import("@/pages/TraceDetail")).TraceDetail }),
      },
      {
        path: "regression",
        lazy: async () => ({ Component: (await import("@/pages/Regression")).RegressionList }),
      },
      {
        path: "regression/:reportId",
        lazy: async () => ({ Component: (await import("@/pages/Regression")).RegressionDetail }),
      },
      { path: "approvals", lazy: async () => ({ Component: (await import("@/pages/Approvals")).Approvals }) },
      { path: "versions", lazy: async () => ({ Component: (await import("@/pages/AgentVersions")).AgentVersions }) },
      { path: "*", lazy: async () => ({ Component: (await import("@/pages/NotFound")).NotFound }) },
    ],
  },
];
