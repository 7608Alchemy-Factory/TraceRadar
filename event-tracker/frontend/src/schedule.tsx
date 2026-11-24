import { useState, type FormEvent } from "react";
import { formatScheduledTime, toScheduledInstant, zonedTimeInput } from "./clock";
import { t } from "./i18n";
import type { Schedule } from "./types";
import { Icon } from "./ui";

export type ScheduleInput = {
  mode: Schedule["mode"];
  times?: string[];
  starts_at?: string;
  interval_minutes?: number;
  agent_mode: boolean;
};

export function scheduleIssue(schedule: Schedule) {
  if (schedule.error === "NO_LIVE_SOURCE") return t("请批准至少一个实时来源。");
  if (schedule.error === "QWEN_NOT_CONFIGURED") return t("请先填写模型服务配置。");
  return "";
}

export function ScheduleBadge({ schedule, timeZone, onClick }: {
  schedule?: Schedule; timeZone: string; onClick: () => void;
}) {
  const working = schedule?.last_run_status === "pending" || schedule?.last_run_status === "running";
  return <button type="button" className="schedule-badge" data-active={!!schedule?.next_at || working}
    onClick={onClick} title={schedule ? scheduleIssue(schedule) || t("管理更新频率") : t("管理更新频率")}>
    <Icon name="clock" size={15} />
    <span>{schedule?.error ? t("定时更新待处理") : working ? t("定时检查中") : schedule?.next_at
      ? t("定时更新已启用") : schedule ? t("定时计划已完成") : t("定时更新未启用")}</span>
    {schedule?.next_at && <time dateTime={schedule.next_at}>
      {t("下次检查 {0}", formatScheduledTime(schedule.next_at, timeZone))}
    </time>}
  </button>;
}

export function ScheduleEditor({ schedule, timeZone, defaultAgent, busy, hasLiveSource, modelReady, onSave, onClear, onModel }: {
  schedule?: Schedule; timeZone: string; defaultAgent: boolean; busy: boolean; hasLiveSource: boolean; modelReady: boolean;
  onSave: (input: ScheduleInput) => void; onClear: () => void; onModel: () => void;
}) {
  const [mode, setMode] = useState<Schedule["mode"]>(schedule?.mode ?? "dates");
  const [dates, setDates] = useState(() => {
    const pending = schedule?.times.filter((value) => Date.parse(value) > Date.now()) ?? [];
    return pending.length ? pending.map((value) => zonedTimeInput(value, timeZone)) : [""];
  });
  const [amount, setAmount] = useState(() => {
    const minutes = schedule?.interval_minutes ?? 60;
    return minutes % 1440 === 0 ? minutes / 1440 : minutes % 60 === 0 ? minutes / 60 : minutes;
  });
  const [unit, setUnit] = useState(() => {
    const minutes = schedule?.interval_minutes ?? 60;
    return minutes % 1440 === 0 ? 1440 : minutes % 60 === 0 ? 60 : 1;
  });
  const [startLater, setStartLater] = useState(() => schedule?.mode === "interval" &&
    !!schedule.next_at && Date.parse(schedule.next_at) > Date.now());
  const [starts, setStarts] = useState(() => startLater && schedule?.next_at ? zonedTimeInput(schedule.next_at, timeZone) : "");
  const [agent, setAgent] = useState(schedule?.agent_mode ?? defaultAgent);
  const [error, setError] = useState("");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (mode === "dates") {
        const times = dates.map((value) => toScheduledInstant(value, timeZone));
        if (new Set(times).size !== times.length) throw Error(t("请为每次检查选择不同的时间。"));
        onSave({ mode, times, agent_mode: agent });
      } else {
        const minutes = amount * unit;
        if (!Number.isInteger(minutes) || minutes < 1 || minutes > 43200) throw Error(t("更新间隔需在 1 分钟到 30 天之间。"));
        onSave({ mode, interval_minutes: minutes, starts_at: startLater ? toScheduledInstant(starts, timeZone) : undefined, agent_mode: agent });
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return <form className="form-stack schedule-editor" onSubmit={submit}>
    <p className="hint">{t("按右上角时区输入时间。定时任务使用真实模型和已批准的实时来源，本地服务运行时才会执行。")}</p>
    <div className="schedule-editor-zone"><Icon name="clock" size={15} /><span>{timeZone}</span></div>
    {!hasLiveSource && <p className="review-callout">{t("请批准至少一个实时来源。")}</p>}
    {!modelReady && <button type="button" className="text-button" onClick={onModel}>{t("打开模型配置")}</button>}
    {error && <p className="error" role="alert">{error}</p>}
    <label>{t("计划类型")}
      <select value={mode} onChange={(event) => setMode(event.target.value === "interval" ? "interval" : "dates")}>
        <option value="dates">{t("指定时间点（最多 3 个）")}</option>
        <option value="interval">{t("按固定频率重复")}</option>
      </select>
    </label>
    {mode === "dates" ? <>
      {dates.map((value, index) => <div className="schedule-date-row" key={index}>
        <label>{t("时间点 {0}", index + 1)}
          <input type="datetime-local" required value={value} onChange={(event) => setDates((previous) =>
            previous.map((item, position) => position === index ? event.target.value : item))} />
        </label>
        {dates.length > 1 && <button type="button" className="icon-button" aria-label={t("移除时间点 {0}", index + 1)}
          onClick={() => setDates((previous) => previous.filter((_, position) => position !== index))}><Icon name="trash" /></button>}
      </div>)}
      {dates.length < 3 && <button className="text-button" type="button" onClick={() => setDates((previous) => [...previous, ""])}>
        <Icon name="plus" size={15} /> {t("添加时间点")}
      </button>}
    </> : <>
      <div className="schedule-date-row">
        <label>{t("每隔")}<input type="number" min="1" required value={amount || ""} onChange={(event) => setAmount(Number(event.target.value))} /></label>
        <label>{t("单位")}<select value={unit} onChange={(event) => setUnit(Number(event.target.value))}>
          <option value={1}>{t("分钟")}</option><option value={60}>{t("小时")}</option><option value={1440}>{t("天")}</option>
        </select></label>
      </div>
      <label>{t("开始时间")}
        <select value={startLater ? "later" : "now"} onChange={(event) => setStartLater(event.target.value === "later")}>
          <option value="now">{t("从现在开始")}</option><option value="later">{t("指定未来时间")}</option>
        </select>
      </label>
      {startLater && <label>{t("首次检查时间")}<input type="datetime-local" required value={starts}
        onChange={(event) => setStarts(event.target.value)} /></label>}
    </>}
    <label className="check-row"><input type="checkbox" checked={agent} onChange={(event) => setAgent(event.target.checked)} />{t("定时检查使用 Agent 模式")}</label>
    <div className="schedule-editor-actions">
      {schedule && <button type="button" disabled={busy} onClick={onClear}>{t("关闭定时更新")}</button>}
      <button type="submit" className="primary" disabled={busy || !hasLiveSource || !modelReady}>{t("保存更新计划")}</button>
    </div>
  </form>;
}
