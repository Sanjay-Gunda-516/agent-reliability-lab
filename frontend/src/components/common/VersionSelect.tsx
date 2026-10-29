import { useAgentVersions } from "@/api/queries";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const ALL = "__all__";

/** Agent version filter; "" means all versions. */
export function VersionSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const versions = useAgentVersions();
  return (
    <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? "" : v)}>
      <SelectTrigger aria-label="Agent version" className="w-44 bg-surface">
        <SelectValue placeholder="All versions" />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL}>All versions</SelectItem>
        {(versions.data ?? []).map((v) => (
          <SelectItem key={v.name} value={v.name}>
            {v.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
