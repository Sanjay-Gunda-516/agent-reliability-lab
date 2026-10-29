import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";

import { TooltipProvider } from "@/components/ui/tooltip";
import { makeQueryClient } from "@/lib/queryClient";
import { routes } from "@/routes";

const router = createBrowserRouter(routes);

export default function App() {
  const [client] = useState(makeQueryClient);
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={200}>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}
