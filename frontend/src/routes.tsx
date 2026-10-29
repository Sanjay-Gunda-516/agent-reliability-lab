import type { RouteObject } from "react-router";

import { AppShell } from "@/components/layout/AppShell";
import { AgentVersions } from "@/pages/AgentVersions";
import { Approvals } from "@/pages/Approvals";
import { NotFound } from "@/pages/NotFound";
import { Overview } from "@/pages/Overview";
import { RegressionDetail, RegressionList } from "@/pages/Regression";
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
      { path: "regression", element: <RegressionList /> },
      { path: "regression/:reportId", element: <RegressionDetail /> },
      { path: "approvals", element: <Approvals /> },
      { path: "versions", element: <AgentVersions /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];
