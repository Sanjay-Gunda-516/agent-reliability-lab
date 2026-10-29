import { cn } from "@/lib/utils";
import { TONE_CLASSES, type StatusStyle } from "@/lib/status";

interface StatusBadgeProps {
  style: StatusStyle;
  className?: string;
  /** Overrides the label, e.g. to add a count. */
  label?: string;
}

/** A status shown with an icon and words, so it never relies on colour alone. */
export function StatusBadge({ style, className, label }: StatusBadgeProps) {
  const Icon = style.icon;
  return (
    <span
      className={cn(
        "inline-flex h-6 shrink-0 items-center gap-1.5 rounded-md border px-2 text-xs font-medium whitespace-nowrap",
        TONE_CLASSES[style.tone],
        className,
      )}
    >
      <Icon aria-hidden className="size-3.5" />
      {label ?? style.label}
    </span>
  );
}
