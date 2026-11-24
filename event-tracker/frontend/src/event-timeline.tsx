import { t } from "./i18n";
import { useLayoutEffect, useRef, useState } from "react";
import { headline, longDate, status, text } from "./display";
import type { EventDetail, TimelineItem, Transition } from "./types";
import { priorityLabel, type Priority } from "./topic-report";
import { useTranslation } from "./reference-translation";

type Tone = "first" | "update" | "resolved" | "review";
type Milestone = {
  id: string;
  at: string;
  dateLabel: string;
  label: string;
  description: string;
  tone: Tone;
  evidenceIds: string[];
  previous?: string;
  next?: string;
  impact: Priority | "review";
  reason?: string;
  origin?: string;
};

const entryKey = (item: TimelineItem) =>
  JSON.stringify([item.occurred_at, item.description, [...item.evidence_ids].sort()]);

function sortDate(a: string, b: string) {
  return Date.parse(a) - Date.parse(b) || a.localeCompare(b);
}

function changeBadge(transition: Transition): Pick<Milestone, "tone" | "label" | "previous" | "next"> {
  const before = transition.before_state?.current_status;
  const after = transition.after_state.current_status;
  if (before === after) return { tone: "update", label: t("进展更新") };
  const next = status(after);
  const previous = before ? status(before) : undefined;
  if (["patched", "completed"].includes(after)) return { tone: "resolved", label: next, previous, next };
  if (!before) return { tone: "first", label: after === "disclosed" ? next : t("首次记录"), next };
  return { tone: "first", label: t("状态变化"), previous, next };
}

function milestones({ event, history, reviews, updates }: EventDetail): Milestone[] {
  const assessments = new Map(updates?.map((update) => [update.transition_id, update]));
  const confirmed = new Map<string, Milestone>();
  for (const item of event.state.timeline) {
    const id = entryKey(item);
    confirmed.set(id, {
      id, at: item.occurred_at, dateLabel: t("进展日期"), tone: "update", label: t("进展更新"),
      description: headline(item.description), evidenceIds: item.evidence_ids, impact: "low",
    });
  }

  const checkpoints: Milestone[] = [];
  for (const transition of history) {
    const oldItems = new Set(transition.before_state?.timeline.map(entryKey));
    const newItems = transition.after_state.timeline
      .filter((item) => !oldItems.has(entryKey(item)))
      .sort((a, b) => sortDate(b.occurred_at, a.occurred_at));
    const update = assessments.get(transition.transition_id);
    const kind = update?.reason_codes[0];
    const impact: Priority = update?.priority === "breaking" ? "breaking" :
      update?.priority === "routine" ? "routine" : "low";
    const badge = {
      ...changeBadge(transition),
      impact: !update && !transition.before_state ? "routine" as const : impact,
      reason: transition.impact_reason ?? (kind === "major_milestone" ? t("核心结论或事件阶段发生实质变化。") :
        kind === "fact_added" ? t("新增可影响理解的事实，保留原有核心结论。") :
        !transition.before_state ? t("首次建立事件及其证据基线。") :
        t("补充记录或细节，对核心结论的影响有限。")),
      origin: t(transition.impact_reason && update?.provider === "qwen" ? "模型归类与说明" :
        update?.provider === "qwen" ? "模型归类 · 规则解释" :
        update?.provider === "human" ? "人工归类 · 规则解释" : "依据已记录的变化"),
    };
    const stage = newItems.find((item) => confirmed.has(entryKey(item)));
    if (stage) {
      Object.assign(confirmed.get(entryKey(stage))!, badge);
    } else if (!newItems.length || badge.tone !== "update") {
      const before = new Set(transition.before_state?.key_facts.map((fact) => fact.fact));
      const added = transition.after_state.key_facts
        .filter((fact) => !before.has(fact.fact))
        .map((fact) => text(fact.fact));
      if (badge.tone === "update" && !added.length) continue;
      checkpoints.push({
        ...badge, id: transition.transition_id, at: transition.committed_at, dateLabel: t("记录时间"),
        description: added.join("\n") || t("事件状态记录已更新。"),
        evidenceIds: transition.evidence_ids,
      });
    }
  }

  const first = [...confirmed.values()].sort((a, b) => sortDate(a.at, b.at))[0];
  if (first?.tone === "update" && !history.length) {
    first.tone = "first";
    first.label = t("首次记录");
  }

  const pending = reviews.map(({ evidence, assessment }): Milestone => ({
    id: `review-${evidence.evidence_id}`, at: evidence.published_at, dateLabel: t("报道日期"),
    description: [headline(evidence.title), ...assessment.conflicts.map(text)].join("\n"),
    label: t("待审核"), tone: "review", evidenceIds: [evidence.evidence_id], impact: "review",
  }));
  return [...confirmed.values(), ...checkpoints, ...pending].sort((a, b) => sortDate(a.at, b.at));
}

export function EventTimeline({
  value, onEvidence,
}: { value: EventDetail; onEvidence: (id: string) => void }) {
  const tr = useTranslation();
  const nodes = milestones(value);
  const [chosenId, setChosenId] = useState("");
  const rail = useRef<HTMLOListElement>(null);
  const selected = nodes.find((node) => node.id === chosenId) ??
    nodes.filter((node) => node.tone !== "review").at(-1) ?? nodes.at(-1);

  useLayoutEffect(() => {
    const list = rail.current;
    const frame = list?.querySelector<HTMLButtonElement>("[aria-pressed='true']")?.parentElement;
    if (!list || !frame) return;
    function keepVisible() {
      if (!list || !frame || !list.clientWidth) return;
      const left = frame.offsetLeft;
      const right = left + frame.offsetWidth;
      if (left < list.scrollLeft) list.scrollLeft = left;
      if (right > list.scrollLeft + list.clientWidth) list.scrollLeft = right - list.clientWidth;
    }
    keepVisible();
    const resize = new ResizeObserver(keepVisible);
    resize.observe(list);
    return () => resize.disconnect();
  }, [selected?.id]);

  const evidence = new Map([
    ...value.evidence.map((item) => [item.evidence_id, item] as const),
    ...value.reviews.map(({ evidence: item }) => [item.evidence_id, item] as const),
  ]);

  return (
    <section className="event-track" aria-labelledby="timeline-title">
      <div className="event-track-heading">
        <h3 id="timeline-title">{t("事件进展")}</h3>
        {!!nodes.length && <span>{nodes.length} {t("个节点 · 从早到晚")}</span>}
      </div>
      {selected ? (
        <>
          <p className="event-track-help">
            {nodes.length > 2 && <span className="timeline-swipe-hint">{t("左右滑动时间轴，")}</span>}
            {t("点选节点查看变化和新闻依据。")}
          </p>
          <ol className="milestone-rail" ref={rail} aria-label={t("事件进展时间轴")}>
            {nodes.map((node, i) => (
              <li key={node.id}>
                <button type="button" data-tone={node.tone} data-impact={node.impact} aria-controls="milestone-detail"
                  aria-pressed={node.id === selected.id} onClick={() => setChosenId(node.id)}
                  onKeyDown={(event) => {
                    const nextIndex = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: nodes.length - 1 }[event.key];
                    if (nextIndex === undefined) return;
                    event.preventDefault();
                    const next = nodes[nextIndex];
                    if (!next) return;
                    setChosenId(next.id);
                    rail.current?.querySelectorAll("button")[nextIndex]?.focus({ preventScroll: true });
                  }}>
                  <span className="milestone-light" aria-hidden="true"><i /></span>
                  <time dateTime={node.at}>{longDate(node.at)}</time>
                  <span className="milestone-label">{node.impact === "review" ? node.label : priorityLabel(node.impact)}</span>
                </button>
              </li>
            ))}
          </ol>
          <div className="milestone-detail" data-tone={selected.tone} data-impact={selected.impact} id="milestone-detail" aria-live="polite">
            <div key={selected.id} className="milestone-content">
              <div className="milestone-meta">
                <span>{selected.impact === "review" ? selected.label : priorityLabel(selected.impact)}</span>
                <time dateTime={selected.at}>{selected.dateLabel} {longDate(selected.at)}</time>
              </div>
              <p>{tr(selected.description)}</p>
              {selected.reason && <p className="milestone-note">{tr(selected.reason)} <small>· {selected.origin}</small></p>}
              {selected.tone === "review" && <p className="milestone-note">{t("这条报道等待核实，事件当前结论仍以已记录的进展为准。")}</p>}
              {selected.next && (
                <details className="milestone-note">
                  <summary>{t("查看前后结论")}</summary>
                  <p>{selected.previous ? t("状态由「{0}」更新为「{1}」。", tr(selected.previous), tr(selected.next)) : t("记录时的状态为「{0}」。", tr(selected.next))}</p>
                </details>
              )}
              {!!selected.evidenceIds.length && (
                <div className="milestone-evidence" aria-label={t("新闻依据")}>
                  {[...new Set(selected.evidenceIds)].map((id) => {
                    const item = evidence.get(id);
                    const label = item && headline(item.title);
                    return (
                      <button key={id} type="button" onClick={() => onEvidence(id)}>
                        {!label || selected.description.startsWith(label) ? t("查看这条新闻的依据") : tr(label)}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </>
      ) : <p className="event-track-help">{t("这条事件暂时没有可显示的进展记录。")}</p>}
    </section>
  );
}
