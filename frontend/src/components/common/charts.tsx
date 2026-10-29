import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { CHART } from "@/lib/chartColors";

const axis = { stroke: CHART.axis, fontSize: 12, tickLine: false, axisLine: false } as const;
const tooltipStyle = {
  borderRadius: 8,
  border: "1px solid var(--border-soft)",
  boxShadow: "var(--shadow-card)",
  fontSize: 12,
};

export interface SeriesSpec {
  key: string;
  label: string;
  color: string;
}

export type Datum = Record<string, string | number | null>;

interface ChartProps {
  data: Datum[];
  xKey: string;
  series: SeriesSpec[];
  height?: number;
  format?: (value: number) => string;
  /** A sentence describing the chart, read by screen readers instead of the SVG. */
  summary: string;
  /** Whole-number ticks, for counts. */
  integers?: boolean;
}

function Frame({ summary, height, children }: { summary: string; height: number; children: ReactNode }) {
  return (
    <figure className="m-0" style={{ height }}>
      <figcaption className="sr-only">{summary}</figcaption>
      <div aria-hidden className="h-full">
        <ResponsiveContainer width="100%" height="100%">
          {children as React.ReactElement}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

export function TrendLine({ data, xKey, series, height = 220, format, summary }: ChartProps) {
  return (
    <Frame summary={summary} height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }}>
        <CartesianGrid stroke={CHART.grid} vertical={false} />
        <XAxis dataKey={xKey} {...axis} />
        <YAxis {...axis} tickFormatter={format} width={56} />
        <Tooltip contentStyle={tooltipStyle} formatter={(v) => (format ? format(Number(v)) : v)} />
        {series.map((s) => (
          <Line
            key={s.key}
            type="monotone"
            dataKey={s.key}
            name={s.label}
            stroke={s.color}
            strokeWidth={2}
            dot={{ r: 3, strokeWidth: 0, fill: s.color }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
            connectNulls
          />
        ))}
      </LineChart>
    </Frame>
  );
}

export function StackedBars({ data, xKey, series, height = 220, format, summary, integers }: ChartProps) {
  return (
    <Frame summary={summary} height={height}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -8 }} barCategoryGap="28%">
        <CartesianGrid stroke={CHART.grid} vertical={false} />
        <XAxis dataKey={xKey} {...axis} />
        <YAxis {...axis} tickFormatter={format} width={56} allowDecimals={!integers} />
        <Tooltip
          contentStyle={tooltipStyle}
          cursor={{ fill: "var(--surface-muted)" }}
          formatter={(v) => (format ? format(Number(v)) : v)}
        />
        {series.length > 1 && <Legend iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />}
        {series.map((s, i) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stackId="stack"
            fill={s.color}
            radius={i === series.length - 1 ? [4, 4, 0, 0] : 0}
            isAnimationActive={false}
          />
        ))}
      </BarChart>
    </Frame>
  );
}
