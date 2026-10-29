import type { RouteObject } from "react-router";

import { AppShell } from "@/components/layout/AppShell";
import { NotFound } from "@/pages/NotFound";
import { Overview } from "@/pages/Overview";

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <Overview /> },
      { path: "*", element: <NotFound /> },
    ],
  },
];
