import type { RouteObject } from "react-router";

import { AppShell } from "@/components/layout/AppShell";
import { NotFound } from "@/pages/NotFound";
import { Overview } from "@/pages/Overview";
import { ResultDetail } from "@/pages/ResultDetail";
import { RunDetail } from "@/pages/RunDetail";
import { ScenarioPage, Scenarios } from "@/pages/Scenarios";
import { TestRuns } from "@/pages/TestRuns";
import { TraceDetail } from "@/pages/TraceDetail";
import { Traces } from "@/pages/Traces";

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Overview /> },
      { path: "runs", element: <TestRuns /> },
      { path: "runs/:runId", element: <RunDetail /> },
      { path: "runs/:runId/results/:variantId", element: <ResultDetail /> },
      { path: "scenarios", element: <Scenarios /> },
      { path: "scenarios/:scenarioId", element: <ScenarioPage /> },
      { path: "traces", element: <Traces /> },
      { path: "traces/:executionId", element: <TraceDetail /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];
