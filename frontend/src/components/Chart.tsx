import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { Columns } from "../store";

export interface SeriesDef {
  key: string;
  label: string;
  color: string;
  dash?: boolean;
  scale?: string;
}

interface Props {
  title: string;
  unit?: string;
  data: Columns;
  version: number;
  series: SeriesDef[];
  xKey?: string;
  height?: number;
  logY?: boolean;
}

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function Chart({ title, unit, data, version, series, xKey = "t", height = 190, logY }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);

  const columns = () => [data[xKey] ?? [], ...series.map((s) => data[s.key] ?? [])] as uPlot.AlignedData;

  useEffect(() => {
    const el = box.current!;
    const axis = { stroke: css("--text-dim"), grid: { stroke: css("--grid"), width: 1 }, ticks: { stroke: css("--grid") }, font: "11px system-ui" };
    const opts: uPlot.Options = {
      width: el.clientWidth || 300,
      height,
      legend: { show: true, live: true },
      cursor: { drag: { x: true, y: false } },
      scales: { x: { time: false }, y: logY ? { distr: 3 } : { auto: true } },
      axes: [
        { ...axis, label: "min", labelSize: 14, labelFont: "11px system-ui" },
        { ...axis, size: 52, label: unit, labelSize: unit ? 14 : 0, labelFont: "11px system-ui" },
      ],
      series: [
        { label: "t" },
        ...series.map((s) => ({
          label: s.label, stroke: s.color, width: s.dash ? 1 : 1.6, dash: s.dash ? [5, 4] : undefined,
          spanGaps: false, points: { show: false },
        })),
      ],
    };
    plot.current = new uPlot(opts, columns(), el);
    const ro = new ResizeObserver(() => plot.current?.setSize({ width: el.clientWidth, height }));
    ro.observe(el);
    return () => { ro.disconnect(); plot.current?.destroy(); plot.current = null; };
    // series identity is fixed per chart instance
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [height, logY]);

  useEffect(() => {
    plot.current?.setData(columns());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, data]);

  return (
    <div className="card chart">
      <div className="card-title">{title}</div>
      <div ref={box} className="chart-box" />
    </div>
  );
}
