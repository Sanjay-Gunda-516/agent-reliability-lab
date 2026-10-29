import { ShieldCheck } from "lucide-react";
import { NavLink } from "react-router";

import { useApprovals } from "@/api/queries";
import { cn } from "@/lib/utils";

import { NAV_ITEMS } from "./nav";

function PendingCount() {
  const pending = useApprovals("pending");
  const n = pending.data?.length ?? 0;
  if (!n) return null;
  return (
    <span
      className="ml-auto rounded-md bg-tan-soft px-1.5 text-xs font-semibold text-tan-strong tabular-nums"
      aria-label={`${n} pending`}
    >
      {n}
    </span>
  );
}

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <span className="flex size-8 items-center justify-center rounded-lg bg-navy text-white">
          <ShieldCheck aria-hidden className="size-4.5" />
        </span>
        <div className="leading-tight">
          <div className="text-sm font-semibold text-navy">Agent Reliability Lab</div>
          <div className="text-xs text-text-muted">Support agent quality</div>
        </div>
      </div>
      <nav aria-label="Main" className="flex flex-1 flex-col gap-0.5 px-3">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={onNavigate}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-200 ease-calm",
                isActive
                  ? "bg-surface text-navy shadow-card"
                  : "text-navy/75 hover:bg-sidebar-accent hover:text-navy",
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon
                  aria-hidden
                  className={cn("size-4", isActive ? "text-blue" : "text-navy/60")}
                />
                {label}
                {to === "/approvals" && <PendingCount />}
              </>
            )}
          </NavLink>
        ))}
      </nav>
      <p className="px-5 py-4 text-xs text-text-muted">Reads the lab API · no fake data</p>
    </div>
  );
}
