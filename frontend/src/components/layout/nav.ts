import {
  Activity,
  GitCompareArrows,
  Layers,
  LayoutDashboard,
  ListChecks,
  PlayCircle,
  UserCheck,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/runs", label: "Test Runs", icon: PlayCircle },
  { to: "/scenarios", label: "Scenarios", icon: ListChecks },
  { to: "/traces", label: "Traces", icon: Activity },
  { to: "/regression", label: "Regression", icon: GitCompareArrows },
  { to: "/approvals", label: "Human Approvals", icon: UserCheck },
  { to: "/versions", label: "Agent Versions", icon: Layers },
];
