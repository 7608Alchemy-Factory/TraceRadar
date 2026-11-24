import { useEffect, useState, type CSSProperties } from "react";
import { api } from "./api";
import { date, title, topicTitle } from "./display";
import { t } from "./i18n";
import { useTranslation } from "./reference-translation";
import type { Topic } from "./types";

type Signal = { event_id: string; topic_id: string; title: string; updated_at: string };

const radarGlyphs = Array.from({ length: 33 }, (_, row) => Array.from({ length: 65 }, (_, column) => {
  const x = (column - 32) / 32;
  const y = (row - 16) / 16;
  const radius = Math.hypot(x, y);
  const angle = Math.atan2(y, x);
  if (radius > .96) return " ";
  if (row === 16 && column === 32) return "+";
  if ((row === 16 || column === 32) && radius > .08) return radius > .78 ? "|" : "·";
  if (radius > .88 && Math.abs(Math.sin(angle * 12)) < .18) return "+";
  if ([.24, .46, .68, .83].some((ring) => Math.abs(radius - ring) < .019)) {
    return Math.cos(angle * 4 + radius * 2) > -.53 ? "·" : " ";
  }
  return column % 5 === 2 && row % 3 === 1 ? "." : " ";
}).join("")).join("\n");

const signalSlots = [[66, 38], [32, 63], [56, 23], [69, 71]];

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
  const recent = known.slice(0, signalSlots.length);
  const currentIndex = Math.max(0, recent.findIndex((item) => item.event_id === selected));
  const current = recent[currentIndex];
  const topic = topics.find((item) => item.topic_id === current?.topic_id);
  return (
    <section className="live-radar" aria-label={t("事件雷达")}>
      <div className="radar-header"><h2>{t("事件雷达")}</h2><span>{known.length} {t("条近期信号")}</span></div>
      <div className="radar-field">
        <div className="radar-feed">
          {recent.length > 0 && <><h3 className="radar-feed-label">{t("近期事件")}</h3><ol>
            {recent.map((signal, index) => (
              <li key={signal.event_id}>
                <button type="button" aria-pressed={current?.event_id === signal.event_id}
                  onClick={() => setSelected(signal.event_id)}>
                  <span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
                  <span>{tr(title(signal.title))}</span>
                </button>
              </li>
            ))}
          </ol></>}
          <div className="radar-readout">
            {current && topic ? (
              <><small><strong>{t("最近更新")}</strong>{" "}{tr(topicTitle(topic.title))} · {date(current.updated_at)}</small>
                <button type="button" onClick={() => onTopic(topic.topic_id)}>{t("查看主题")}</button></>
            ) : <small>{t(signals ? "暂无事件信号" : "正在读取事件信号…")}</small>}
          </div>
        </div>
        <div className="radar-disc" aria-hidden="true">
          <span className="radar-reticle" />
          <span className="radar-range-rings" />
          <pre className="radar-glyph-field">{radarGlyphs}</pre>
          <span className="radar-sweep" />
          {current && <span key={current.event_id} className="radar-blip"
            style={{ "--x": `${signalSlots[currentIndex][0]}%`, "--y": `${signalSlots[currentIndex][1]}%` } as CSSProperties} />}
        </div>
      </div>
    </section>
  );
}
