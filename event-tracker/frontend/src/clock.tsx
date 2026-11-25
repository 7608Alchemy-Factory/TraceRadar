import { useEffect, useMemo, useState } from "react";
import { locale, t } from "./i18n";

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

export function useWorkspaceZone() {
  const [zone, setZone] = useState<string>(() => {
    const saved = localStorage.getItem("traceradar-timezone");
    return zones.find(([id]) => id === saved)?.[0] ?? "Asia/Hong_Kong";
  });
  return {
    zone,
    timeZone: zones.find(([id]) => id === zone)?.[2] ?? "Asia/Hong_Kong",
    setZone: (value: string) => {
      if (!zones.some(([id]) => id === value)) return;
      setZone(value);
      localStorage.setItem("traceradar-timezone", value);
    },
  };
}

export function formatScheduledTime(value: string, timeZone: string) {
  return new Intl.DateTimeFormat(locale(), {
    timeZone, year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZoneName: "shortOffset",
  }).format(new Date(value)).replace(/\bGMT/, "UTC");
}

export function zonedTimeInput(value: string | Date, timeZone: string) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en", {
    timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(value)).map(({ type, value: part }) => [type, part]));
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export function toScheduledInstant(value: string, timeZone: string) {
  const wall = Date.parse(`${value}Z`);
  if (!Number.isFinite(wall)) throw Error(t("请选择有效的未来时间。"));
  // Sample both sides of a possible daylight saving change, then round-trip each candidate.
  const day = 86_400_000;
  const candidates = new Set([-day, 0, day].map((delta) => {
    const sample = wall + delta;
    const offset = Date.parse(`${zonedTimeInput(new Date(sample), timeZone)}Z`) - sample;
    return wall - offset;
  }));
  const matches = [...candidates].filter((candidate) => zonedTimeInput(new Date(candidate), timeZone) === value);
  if (matches.length !== 1) throw Error(t("该时间受夏令时切换影响，请选择附近的其他时间。"));
  if (matches[0] <= Date.now()) throw Error(t("请选择有效的未来时间。"));
  return new Date(matches[0]).toISOString();
}

export function WorkspaceClock({ zone, timeZone, setZone }: ReturnType<typeof useWorkspaceZone>) {
  const [now, setNow] = useState(() => new Date());
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
          <select value={zone} onChange={(event) => setZone(event.target.value)}>
            {zones.map(([id, city]) => <option key={id} value={id}>{t(city)}</option>)}
          </select>
        </label>
      </div>
    </details>
  );
}
