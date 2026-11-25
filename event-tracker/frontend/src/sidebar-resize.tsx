import { useEffect, useState } from "react";
import { t } from "./i18n";

const minimum = 190;
const maximum = () => Math.max(minimum, Math.min(420, Math.floor(window.innerWidth * .44)));
const clampWidth = (width: number) => Math.max(minimum, Math.min(maximum(), width));

export function useSidebarWidth() {
  const [width, setWidth] = useState(() => Number(localStorage.getItem("traceradar-sidebar-width")) || 236);
  const [limit, setLimit] = useState(maximum);
  useEffect(() => {
    function resize() {
      setLimit(maximum());
    }
    window.addEventListener("resize", resize);
    return () => window.removeEventListener("resize", resize);
  }, []);
  function save(next: number) {
    const value = clampWidth(next);
    setWidth(value);
    localStorage.setItem("traceradar-sidebar-width", String(value));
  }
  return { width: Math.max(minimum, Math.min(limit, width)), limit, save, setWidth };
}

export function SidebarHandle({ width, limit, save, setWidth }: ReturnType<typeof useSidebarWidth>) {
  return (
    <div className="sidebar-resize" tabIndex={0} role="separator" aria-orientation="vertical"
      aria-label={t("调整侧栏宽度")} aria-valuemin={minimum} aria-valuemax={limit} aria-valuenow={width}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
      }}
      onPointerMove={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) setWidth(clampWidth(Math.round(event.clientX)));
      }}
      onPointerUp={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) save(Math.round(event.clientX));
      }}
      onKeyDown={(event) => {
        const next = { ArrowLeft: width - 16, ArrowRight: width + 16, Home: minimum, End: limit }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        save(next);
      }}>
      <span aria-hidden="true" />
    </div>
  );
}
