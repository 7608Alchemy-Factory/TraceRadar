import React from "react";
import type { EventDetail, EvidenceDecision } from "./types";
import {
  date,
  text,
  status,
  title,
  safeUrl,
  pretty,
  headline,
} from "./display";
import { Icon } from "./ui";
import { EventTimeline } from "./event-timeline";

export function EventView({
  value,
  onEvidence,
}: {
  value: EventDetail;
  onEvidence: (id: string) => void;
}) {
  const e = value.event;
  const latestTransition = value.history[value.history.length - 1];
  const latestRunTransitions = latestTransition?.run_id
    ? value.history.filter((h) => h.run_id === latestTransition.run_id)
    : latestTransition
      ? [latestTransition]
      : [];
  const beforeLatestRunFacts =
    latestRunTransitions[0]?.before_state?.key_facts ?? [];
  const latestAddedFactSet = new Set(
    latestTransition
      ? latestTransition.after_state.key_facts
          .filter(
            (f) =>
              !beforeLatestRunFacts.some(
                (x) => JSON.stringify(x) === JSON.stringify(f),
              ),
          )
          .map((f) => JSON.stringify(f))
      : [],
  );
  const newestFactsFirst = [...e.state.key_facts].reverse();
  const newestEvidenceFirst = [...value.evidence].sort((a, b) => {
    const ta = Date.parse(a.published_at);
    const tb = Date.parse(b.published_at);
    if (!Number.isNaN(ta) && !Number.isNaN(tb) && tb !== ta) return tb - ta;
    return b.published_at.localeCompare(a.published_at);
  });
  return (
    <div>
      <div className="event-lead">
        <span className="meta">
          版本 v{e.version} · 更新于 {date(e.updated_at)}
        </span>
        <h2>{title(e.title)}</h2>
        <div
          className={`tag ${e.state.current_status === "patched" ? "green" : ""}`}
        >
          <strong>当前状态 · </strong>
          {status(e.state.current_status)}
        </div>
      </div>
      <EventTimeline value={value} onEvidence={onEvidence} />
      <div className="event-detail-grid">
        <div>
          <section className="detail-section">
            <h3>
              当前事实 <span className="count">{e.state.key_facts.length}</span>
            </h3>
            {!newestFactsFirst.length && <p className="hint">暂时没有提炼出的事实。</p>}
            <ul className="fact-list">
              {newestFactsFirst.map((f, i) => {
                const isNew = latestAddedFactSet.has(JSON.stringify(f));
                return (
                  <li key={i} className={isNew ? "fact-new" : ""}>
                    <span>
                      {isNew && <span className="new-pill">本轮新增</span>}
                      {text(f.fact)}
                    </span>
                    <span className="citation-group">
                      {f.evidence_ids.map((id) => (
                        <button
                          className="citation"
                          key={id}
                          onClick={() => onEvidence(id)}
                          aria-label={`查看事实 ${i + 1} 的证据`}
                        >
                          {e.state.sources.includes(id) ? e.state.sources.indexOf(id) + 1 : "依据"}
                        </button>
                      ))}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
          {!!value.history.length && <details className="detail-section audit-history">
            <summary>状态修改记录 <span className="count">{value.history.length}</span></summary>
            {[...value.history].reverse().map((h) => {
              const added = h.after_state.key_facts.filter(
                (f) =>
                  !h.before_state?.key_facts.some(
                    (x) => JSON.stringify(x) === JSON.stringify(f),
                  ),
              );
              const removed =
                h.before_state?.key_facts.filter(
                  (f) =>
                    !h.after_state.key_facts.some(
                      (x) => JSON.stringify(x) === JSON.stringify(f),
                    ),
                ) ?? [];
              return (
                <details className="history-row" key={h.transition_id}>
                  <summary>
                    <span>
                      {status(h.before_state?.current_status ?? "新事件")}{" "}
                      <span className="muted">→</span>{" "}
                      {status(h.after_state.current_status)}
                    </span>
                    <time>{date(h.committed_at)}</time>
                  </summary>
                  {added.map((f, i) => (
                    <p className="diff-added" key={i}>
                      ＋ {text(f.fact)}
                    </p>
                  ))}
                  {removed.map((f, i) => (
                    <p className="diff-removed" key={i}>
                      − {text(f.fact)}
                    </p>
                  ))}
                  <details className="quiet-details">
                    <summary>完整修改记录</summary>
                    <pre>{pretty(h)}</pre>
                  </details>
                </details>
              );
            })}
          </details>}
        </div>
        <aside className="evidence-rail">
          <h3>
            关联证据 <span className="count">{value.evidence.length}</span>
          </h3>
          <p className="hint">
            选择标题查看原文。事实旁的数字对应新闻编号。
          </p>
          {newestEvidenceFirst.map((ev) => (
            <button
              className="evidence-reference"
              key={ev.evidence_id}
              onClick={() => onEvidence(ev.evidence_id)}
            >
              <span className="reference-number">
                {e.state.sources.includes(ev.evidence_id)
                  ? e.state.sources.indexOf(ev.evidence_id) + 1
                  : "·"}
              </span>
              <span>
                <strong>{headline(ev.title)}</strong>
                <small>
                  {ev.source_id} · {date(ev.published_at, false)}
                </small>
                <span
                  className={`tag ${ev.relation === "conflicting" ? "amber" : ""}`}
                >
                  {{
                    supporting: "支持证据",
                    conflicting: "冲突证据",
                    duplicate: "重复报道",
                  }[ev.relation ?? ""] ?? ev.relation}
                </span>
              </span>
              <Icon name="arrow" size={15} />
            </button>
          ))}
        </aside>
      </div>
    </div>
  );
}

export function EvidenceView({ value }: { value: EvidenceDecision }) {
  const { evidence: e, assessment: a } = value;
  const url = safeUrl(e.url);
  const decisions = a ? ("event_decisions" in a ? a.event_decisions : [a]) : [];
  return (
    <article className="evidence-full">
      <div className="row-between">
        <span className="tag">{e.source_id}</span>
        <time>{date(e.published_at)}</time>
      </div>
      <h2>{headline(e.title)}</h2>
      <p className="article-body">{text(e.content)}</p>
      {url && (
        <a
          className="external-link"
          href={url}
          target="_blank"
          rel="noreferrer"
        >
          原始链接 <Icon name="external" size={14} />
        </a>
      )}
      <dl className="metadata-list">
        <div>
          <dt>发布时间</dt>
          <dd>{date(e.published_at)}</dd>
        </div>
        <div>
          <dt>获取时间</dt>
          <dd>{date(e.fetched_at)}</dd>
        </div>
        <div>
          <dt>当前处理</dt>
          <dd>
            {value.status === "review"
              ? "待审核"
              : value.status === "failed"
                ? "处理失败"
                : value.status === "failed_retryable"
                  ? "服务故障，可重试"
                  : value.status === "pending"
                    ? "未处理"
                    : a?.is_relevant === false
                      ? "已排除"
                      : e.canonical_evidence_id !== e.evidence_id && !a
                        ? "重复报道"
                        : "已处理"}
          </dd>
        </div>
      </dl>
      {value.error != null && (
        <div role="alert" className="error">
          {typeof value.error === "object" && "message" in value.error
            ? String(value.error.message)
            : "处理失败，请查看运行记录。"}
        </div>
      )}
      {a && (
        <div className="assessment-note">
          <h3>为什么这样处理</h3>
          <p>{a.is_relevant ? "与追踪目标相关。" : "判断为与追踪目标无关。"}</p>
          {decisions.map((decision, i) => (
            <div key={i}>
              {decision.association.title && <strong>{decision.association.title}</strong>}
              <p>{decision.association.reason}</p>
              {decision.conflicts.map((conflict, j) => <p key={j}>{text(conflict)}</p>)}
            </div>
          ))}
        </div>
      )}
      <details className="quiet-details">
        <summary>原始内容与技术记录</summary>
        <p className="raw-text">{e.content}</p>
        <pre>{pretty(value)}</pre>
      </details>
    </article>
  );
}
