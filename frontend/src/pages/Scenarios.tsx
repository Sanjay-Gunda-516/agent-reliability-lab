import { ArrowLeft } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { useScenario, useScenarios } from "@/api/queries";
import type { ScenarioDetail, ScenarioSummary } from "@/api/types";
import { KeyValues, Mono, RawDetails, Tags, Value } from "@/components/common/Details";
import { FilterBar, FilterSelect, SearchInput } from "@/components/common/Filters";
import { PageHeader } from "@/components/common/PageHeader";
import { Panel } from "@/components/common/Panel";
import { EmptyState, QueryState } from "@/components/common/QueryState";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { dateTime, humanize, shortHash } from "@/lib/format";

function ScenarioTable({ scenarios }: { scenarios: ScenarioSummary[] }) {
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState("");
  const tags = useMemo(
    () => [...new Set(scenarios.flatMap((s) => s.tags))].sort().map((t) => ({ value: t, label: t })),
    [scenarios],
  );
  const q = search.toLowerCase();
  const shown = scenarios.filter(
    (s) =>
      (!tag || s.tags.includes(tag)) &&
      (!q || s.id.includes(q) || s.title.toLowerCase().includes(q)),
  );
  return (
    <>
      <FilterBar>
        <SearchInput value={search} onChange={setSearch} label="Search scenarios" placeholder="Search scenarios" />
        <FilterSelect label="Tag" value={tag} onChange={setTag} options={tags} allLabel="All tags" />
        <span className="text-xs text-text-muted">
          {shown.length} of {scenarios.length} scenarios ·{" "}
          {shown.reduce((n, s) => n + s.variants.length, 0)} variants
        </span>
      </FilterBar>
      <Panel flush>
        {shown.length === 0 ? (
          <EmptyState message="No scenarios match these filters." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scenario</TableHead>
                <TableHead>Tags</TableHead>
                <TableHead>Expected outcome</TableHead>
                <TableHead>Expected status</TableHead>
                <TableHead>Personas</TableHead>
                <TableHead>Content hash</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((s) => (
                <TableRow key={s.id}>
                  <TableCell className="max-w-sm whitespace-normal">
                    <Link to={`/scenarios/${s.id}`} className="font-medium text-blue-strong hover:underline">
                      {s.id}
                    </Link>
                    <div className="text-xs text-text-muted">{s.title}</div>
                  </TableCell>
                  <TableCell><Tags tags={s.tags} /></TableCell>
                  <TableCell className="text-navy">{humanize(s.expected_outcome)}</TableCell>
                  <TableCell className="text-navy">{humanize(s.expected_status)}</TableCell>
                  <TableCell className="text-text-muted">{s.variants.map((v) => v.persona).join(", ")}</TableCell>
                  <TableCell><Mono title={s.content_hash}>{shortHash(s.content_hash)}</Mono></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Panel>
    </>
  );
}

export function Scenarios() {
  const scenarios = useScenarios();
  return (
    <>
      <PageHeader
        title="Scenarios"
        description="The test catalogue, as last synced from backend/scenarios/*.yaml. Results for each run are under Test runs."
      />
      <QueryState
        query={scenarios}
        isEmpty={(d) => d.length === 0}
        emptyMessage="No scenarios synced yet."
        emptyHint="Run `python -m app.scenarios sync`, or start any test run."
      >
        {(data) => <ScenarioTable scenarios={data} />}
      </QueryState>
    </>
  );
}

function ToolList({ tools, never }: { tools?: { tool: string; args: Record<string, unknown> | null; result: string }[]; never?: boolean }) {
  if (!tools?.length) return <span className="text-text-muted">none</span>;
  return (
    <ul className="flex flex-col gap-1">
      {tools.map((t, i) => (
        <li key={i}>
          <Mono>{t.tool}</Mono>
          {t.args && <span className="text-xs text-text-muted"> <Value value={t.args} /></span>}
          <span className="text-xs text-text-muted">
            {never ? (t.result === "any" ? " · never attempted" : ` · never ${t.result}`) : ` · result ${t.result}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ScenarioView({ s }: { s: ScenarioDetail }) {
  const e = s.definition.expected;
  return (
    <>
      <PageHeader title={s.id} description={s.title} actions={<Tags tags={s.tags} />} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Expected behaviour">
          <p className="text-sm text-navy">{e.behavior}</p>
          <KeyValues
            className="mt-4"
            items={[
              { label: "Outcome", value: humanize(e.outcome) },
              { label: "Final status", value: humanize(e.final_status) },
              { label: "Escalation reason", value: humanize(e.escalation_reason) },
              { label: "First-turn intent", value: humanize(e.intent) },
            ]}
          />
        </Panel>
        <Panel title="Tools and state">
          <KeyValues
            className="sm:grid-cols-1"
            items={[
              { label: "Must call", value: <ToolList tools={e.must_call} /> },
              { label: "Must not call", value: <ToolList tools={e.must_not_call} never /> },
              { label: "State afterwards", value: <Value value={e.state} /> },
              { label: "Reply", value: <Value value={e.response} /> },
            ]}
          />
        </Panel>
        <Panel title="Variants" description="One conversation per persona">
          <ul className="flex flex-col gap-3">
            {s.variants.map((v) => (
              <li key={v.id}>
                <div className="mb-1 text-xs font-medium text-text-muted">{v.persona}</div>
                {v.turns.map((t, i) => (
                  <p key={i} className="mb-1 rounded-lg bg-blue-soft px-3 py-2 text-sm text-navy">{t}</p>
                ))}
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Version">
          <KeyValues
            items={[
              { label: "Customer", value: s.customer_id },
              { label: "Content hash", value: shortHash(s.content_hash), mono: true },
              { label: "Synced", value: dateTime(s.updated_at) },
              { label: "Active", value: s.is_active ? "yes" : "no" },
            ]}
          />
          <div className="mt-4">
            <RawDetails label="Full definition" value={s.definition} />
          </div>
        </Panel>
      </div>
    </>
  );
}

export function ScenarioPage() {
  const id = useParams().scenarioId ?? "";
  const scenario = useScenario(id);
  return (
    <>
      <Link to="/scenarios" className="mb-3 inline-flex items-center gap-1 text-xs font-medium text-text-muted hover:text-navy">
        <ArrowLeft aria-hidden className="size-3.5" /> Scenarios
      </Link>
      <QueryState query={scenario}>{(s) => <ScenarioView s={s} />}</QueryState>
    </>
  );
}
