import { useEffect, useState, type CSSProperties } from "react";
import { api } from "./api";
import { date, title, topicTitle } from "./display";
import { t } from "./i18n";
import { useTranslation } from "./reference-translation";
import type { Topic } from "./types";

type Signal = { event_id: string; topic_id: string; title: string; updated_at: string };

const radarGlyphs = Array.from({ length: 27 }, (_, row) => Array.from({ length: 55 }, (_, column) => {
  const x = (column - 27) / 27;
  const y = (row - 13) / 13;
  const radius = Math.hypot(x, y);
  if (radius > 1.02) return " ";
  if (row === 13 || column === 27) return "+";
  if ([.26, .5, .73, .97].some((ring) => Math.abs(radius - ring) < .025)) return (row + column) % 5 === 0 ? "x" : ":";
  return column % 3 === 0 && row % 2 === 0 ? "." : " ";
}).join("")).join("\n");

export function EventRadar({ topics, onTopic }: { topics: Topic[]; onTopic: (id: string) => void }) {
  const [signals, setSignals] = useState<Signal[]>();
  const [selected, setSelected] = useState("");
  const tr = useTranslation();
  useEffect(() => {
    const request = new AbortController();
    function refresh() {
      if (document.hidden) return;
      api<Signal[]>("/radar", { signal: request.signal }).then(setSignals).catch(() => {
        if (!request.signal.aborted) setSignals((previous) => previous ?? []);
      });
    }
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
      request.abort();
    };
  }, []);
  const known = (signals ?? []).filter((item) => topics.some((topic) => topic.topic_id === item.topic_id));
  const current = known.find((item) => item.event_id === selected) ?? known[0];
  const topic = topics.find((item) => item.topic_id === current?.topic_id);
  return (
    <section className="live-radar" aria-label={t("事件雷达")}>
      <div className="radar-header"><h2>{t("事件雷达")}</h2><span>{known.length} {t("条近期信号")}</span></div>
      <div className="radar-field">
        <div className="radar-feed">
          {known.length > 0 && <ol>
            {known.slice(0, 4).map((signal, index) => (
              <li key={signal.event_id}>
                <button type="button" aria-pressed={current?.event_id === signal.event_id}
                  onClick={() => setSelected(signal.event_id)}>
                  <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <span>{tr(title(signal.title))}</span>
                </button>
              </li>
            ))}
          </ol>}
          <div className="radar-readout">
            {current && topic ? (
              <><small>{tr(topicTitle(topic.title))} · {date(current.updated_at)}</small>
                <button type="button" onClick={() => onTopic(topic.topic_id)}>{t("查看主题")}</button></>
            ) : <small>{t(signals ? "暂无事件信号" : "正在读取事件信号…")}</small>}
          </div>
        </div>
        <div className="radar-disc">
          <pre className="radar-glyph-field" aria-hidden="true">{radarGlyphs}</pre>
          <span className="radar-sweep" aria-hidden="true" />
          {known.map((signal, index) => {
            const angle = .35 + index * 2.4;
            const radius = 18 + index * 3;
            return (
              <button key={signal.event_id} type="button" className="radar-point"
                aria-label={tr(title(signal.title))} title={tr(title(signal.title))}
                aria-pressed={current?.event_id === signal.event_id}
                style={{ "--x": `${50 + Math.cos(angle) * radius}%`, "--y": `${50 + Math.sin(angle) * radius}%` } as CSSProperties}
                onClick={() => setSelected(signal.event_id)}><span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span></button>
            );
          })}
        </div>
      </div>
    </section>
  );
}
