import type { ReactNode } from "react";

type Metric = { value: number; label: string; open: () => void };

export function OverviewSummary({ metrics }: { metrics: Metric[] }) {
  return (
    <section className="overview-summary" aria-labelledby="overview-summary-title">
      <h2 id="overview-summary-title">追踪概况</h2>
      <div className="overview-metrics">
        {metrics.map(({ value, label, open }) => (
          <button type="button" key={label} onClick={open}>
            <span>{label}</span>
            <strong>{value.toLocaleString("zh-HK")}</strong>
          </button>
        ))}
      </div>
    </section>
  );
}

export function OverviewSection({
  title,
  label,
  onOpen,
  children,
}: {
  title: string;
  label: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  return (
    <section className="section-block overview-section">
      <div className="section-title">
        <h2>{title}</h2>
        <button type="button" className="overview-link" onClick={onOpen}>
          {label}
        </button>
      </div>
      {children}
    </section>
  );
}
