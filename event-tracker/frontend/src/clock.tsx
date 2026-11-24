import { useEffect, useMemo, useState } from "react";
import { t } from "./i18n";

const zones = [
  ["Asia/Hong_Kong", "香港", "Asia/Hong_Kong"],
  ["beijing", "北京", "Asia/Shanghai"],
  ["Asia/Shanghai", "上海", "Asia/Shanghai"],
  ["shenzhen", "深圳", "Asia/Shanghai"],
  ["Asia/Taipei", "台北", "Asia/Taipei"],
  ["Asia/Singapore", "新加坡", "Asia/Singapore"],
  ["Asia/Tokyo", "东京", "Asia/Tokyo"],
  ["Asia/Seoul", "首尔", "Asia/Seoul"],
  ["Asia/Kolkata", "新德里", "Asia/Kolkata"],
  ["Asia/Dubai", "迪拜", "Asia/Dubai"],
  ["Australia/Sydney", "悉尼", "Australia/Sydney"],
  ["Europe/London", "伦敦", "Europe/London"],
  ["Europe/Paris", "巴黎", "Europe/Paris"],
  ["Africa/Johannesburg", "约翰内斯堡", "Africa/Johannesburg"],
  ["America/New_York", "纽约", "America/New_York"],
  ["America/Chicago", "芝加哥", "America/Chicago"],
  ["America/Los_Angeles", "洛杉矶", "America/Los_Angeles"],
  ["America/Sao_Paulo", "圣保罗", "America/Sao_Paulo"],
  ["UTC", "UTC", "UTC"],
] as const;

export function WorkspaceClock() {
  const [zone, setZone] = useState(() => {
    const saved = localStorage.getItem("traceradar-timezone");
    return zones.find(([id]) => id === saved)?.[0] ?? "Asia/Hong_Kong";
  });
  const [now, setNow] = useState(() => new Date());
  const timeZone = zones.find(([id]) => id === zone)?.[2];
  const format = useMemo(() => new Intl.DateTimeFormat("en", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", timeZoneName: "shortOffset",
  }), [timeZone]);
  const parts = Object.fromEntries(format.formatToParts(now).map(({ type, value }) => [type, value]));
  const stamp = `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 250);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <details className="workspace-clock">
      <summary aria-label={t("实时时钟与时区")}>
        <time dateTime={now.toISOString()}>{stamp}</time>
        <span className="clock-offset">{parts.timeZoneName.replace(/^GMT/, "UTC")}</span>
      </summary>
      <div className="clock-options">
        <label>{t("时区")}
          <select value={zone} onChange={(event) => {
            const next = zones.find(([id]) => id === event.target.value);
            if (!next) return;
            setZone(next[0]);
            localStorage.setItem("traceradar-timezone", next[0]);
          }}>
            {zones.map(([id, city]) => <option key={id} value={id}>{t(city)}</option>)}
          </select>
        </label>
      </div>
    </details>
  );
}
