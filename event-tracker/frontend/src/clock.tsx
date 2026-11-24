import { useEffect, useMemo, useState } from "react";
import { locale, t } from "./i18n";

const zones = Array.from(new Set(["UTC", "Asia/Hong_Kong", Intl.DateTimeFormat().resolvedOptions().timeZone,
  ...Intl.supportedValuesOf("timeZone")]));

export function WorkspaceClock() {
  const [zone, setZone] = useState(() => {
    const saved = localStorage.getItem("traceradar-timezone");
    return zones.find((item) => item === saved) ?? "Asia/Hong_Kong";
  });
  const [now, setNow] = useState(() => new Date());
  const [query, setQuery] = useState("");
  const language = locale();
  const format = useMemo(() => new Intl.DateTimeFormat(language, {
    timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }), [language, zone]);
  const parts = Object.fromEntries(format.formatToParts(now).map(({ type, value }) => [type, value]));
  const stamp = `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 250);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <details className="workspace-clock">
      <summary aria-label={t("实时时钟与时区")}>
        <time dateTime={now.toISOString()}>{stamp}</time><small>{zone}</small>
      </summary>
      <div className="clock-options">
        <label>{t("搜索时区")}<input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Asia/Hong_Kong" /></label>
        <label>{t("时区")}
          <select value={zone} onChange={(event) => {
            setZone(event.target.value);
            localStorage.setItem("traceradar-timezone", event.target.value);
            setQuery("");
          }}>
            {zones.filter((item) => item === zone || item.toLowerCase().includes(query.trim().toLowerCase()))
              .map((item) => <option key={item}>{item}</option>)}
          </select>
        </label>
      </div>
    </details>
  );
}
