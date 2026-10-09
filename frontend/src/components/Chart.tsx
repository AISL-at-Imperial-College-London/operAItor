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
  bare?: boolean;   // no card frame (used inside another card)
}

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function Chart({ title, unit, data, version, series, xKey = "t", height = 170, logY, bare }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);

  const columns = () => [data[xKey] ?? [], ...series.map((s) => data[s.key] ?? [])] as uPlot.AlignedData;

  useEffect(() => {
    const el = box.current!;
    const axis = { stroke: css("--text-dim"), grid: { stroke: css("--grid"), width: 1 }, ticks: { stroke: css("--grid") }, font: "11px system-ui" };
    const opts: uPlot.Options = {
      width: el.clientWidth || 300,
      height,
      legend: { show: true, live: false },
      cursor: { drag: { x: true, y: false } },
      scales: { x: { time: false }, y: logY ? { distr: 3 } : { auto: true } },
      axes: [
        { ...axis, size: 28 },
        { ...axis, size: 50 },
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
    <div className={bare ? "chart bare" : "card chart"}>
      <div className="chart-title">{title}{unit ? <span className="dim"> · {unit}</span> : null}<span className="dim x-unit">time [min]</span></div>
      <div ref={box} className="chart-box" />
    </div>
  );
}
