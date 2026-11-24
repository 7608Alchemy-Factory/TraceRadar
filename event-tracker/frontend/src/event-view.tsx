import { ReferenceTranslation, useTranslation } from "./reference-translation";
import { t } from "./i18n";
import React, { useEffect, useState } from "react";
import type { Event, EventDetail, EvidenceDecision } from "./types";
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
  otherEvents = [],
  disabled = false,
  onPatchTitle,
  onDismiss,
  onMerge,
}: {
  value: EventDetail;
  onEvidence: (id: string) => void;
  otherEvents?: Event[];
  disabled?: boolean;
  onPatchTitle?: (nextTitle: string, locked?: boolean) => Promise<void>;
  onDismiss?: (payload: {
    disposition: "irrelevant" | "unwanted";
    reason: string;
    add_exclusion: boolean;
  }) => Promise<void>;
  onMerge?: (payload: {
    target_event_id: string;
    new_title?: string;
    reason?: string;
  }) => Promise<void>;
}) {
  const tr = useTranslation();
  const e = value.event;
  const [panel, setPanel] = useState<"none" | "rename" | "merge" | "dismiss">("none");
  const [draftTitle, setDraftTitle] = useState(e.title);
  const [lockTitle, setLockTitle] = useState(true);
  const [disposition, setDisposition] = useState<"irrelevant" | "unwanted">("irrelevant");
  const [dismissReason, setDismissReason] = useState("");
  const [addExclusion, setAddExclusion] = useState(true);
  const [mergeTargetId, setMergeTargetId] = useState(otherEvents[0]?.event_id ?? "");
  const [mergeTitle, setMergeTitle] = useState("");
  const [mergeReason, setMergeReason] = useState("");

  useEffect(() => {
    setDraftTitle(e.title);
    setLockTitle(true);
    setPanel("none");
    setDismissReason("");
    setMergeTitle("");
    setMergeReason("");
    setMergeTargetId(otherEvents[0]?.event_id ?? "");
  }, [e.event_id, e.title]);

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
        <div className="row-between">
          <span className="meta">
            {t("版本 v")}{e.version} {t("· 更新于")} {date(e.updated_at)}
            {e.title_locked && <span className="tag quiet lock-badge">{t("标题已锁定")}</span>}
          </span>
          {(onPatchTitle || onMerge || onDismiss) && (
            <div className="event-manage-bar">
              {onPatchTitle && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    setDraftTitle(e.title);
                    setLockTitle(true);
                    setPanel(panel === "rename" ? "none" : "rename");
                  }}
                >
                  {t("修改标题")}
                </button>
              )}
              {onMerge && otherEvents.length > 0 && (
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => {
                    setMergeTargetId((prev) =>
                      otherEvents.some((x) => x.event_id === prev)
                        ? prev
                        : otherEvents[0]?.event_id ?? "",
                    );
                    setPanel(panel === "merge" ? "none" : "merge");
                  }}
                >
                  {t("合并到其他事件")}
                </button>
              )}
              {onDismiss && (
                <button
                  type="button"
                  className="danger-ghost"
                  disabled={disabled}
                  onClick={() => setPanel(panel === "dismiss" ? "none" : "dismiss")}
                >
                  {t("移除事件")}
                </button>
              )}
            </div>
          )}
        </div>
        <h2>{tr(title(e.title))}</h2>
        <div
          className={`tag ${e.state.current_status === "patched" ? "green" : ""}`}
        >
          <strong>{t("当前状态 ·")} </strong>
          {tr(status(e.state.current_status))}
        </div>
        {panel === "rename" && onPatchTitle && (
          <form
            className="event-manage-panel form-stack"
            onSubmit={(ev) => {
              ev.preventDefault();
              if (!draftTitle.trim()) return;
              void onPatchTitle(draftTitle.trim(), lockTitle).then(() => setPanel("none"));
            }}
          >
            <label>
              {t("新的事件标题")}
              <input
                required
                maxLength={200}
                value={draftTitle}
                onChange={(ev) => setDraftTitle(ev.target.value)}
              />
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={lockTitle}
                onChange={(ev) => setLockTitle(ev.target.checked)}
              />
              {t("锁定标题（后续模型更新不再自动改写此标题）")}
            </label>
            <div className="action-row">
              <button className="primary" disabled={disabled || !draftTitle.trim()}>
                {t("保存标题")}
              </button>
              <button type="button" onClick={() => setPanel("none")}>
                {t("取消")}
              </button>
            </div>
          </form>
        )}
        {panel === "merge" && onMerge && otherEvents.length > 0 && (
          <form
            className="event-manage-panel form-stack"
            onSubmit={(ev) => {
              ev.preventDefault();
              if (!mergeTargetId) return;
              void onMerge({
                target_event_id: mergeTargetId,
                ...(mergeTitle.trim() ? { new_title: mergeTitle.trim() } : {}),
                ...(mergeReason.trim() ? { reason: mergeReason.trim() } : {}),
              }).then(() => setPanel("none"));
            }}
          >
            <label>
              {t("将当前事件的事实、时间线与证据合并到")}
              <select
                value={mergeTargetId}
                onChange={(ev) => setMergeTargetId(ev.target.value)}
              >
                {otherEvents.map((other) => (
                  <option key={other.event_id} value={other.event_id}>
                    {other.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("合并后的新标题（可选，留空保留目标事件原标题）")}
              <input
                maxLength={200}
                value={mergeTitle}
                onChange={(ev) => setMergeTitle(ev.target.value)}
                placeholder={otherEvents.find((x) => x.event_id === mergeTargetId)?.title ?? ""}
              />
            </label>
            <label>
              {t("合并说明（可选）")}
              <input
                maxLength={240}
                value={mergeReason}
                onChange={(ev) => setMergeReason(ev.target.value)}
                placeholder={t("例如：同一转会期的重复拆条，合并追踪")}
              />
            </label>
            <div className="action-row">
              <button className="primary" disabled={disabled || !mergeTargetId}>
                {t("确认合并")}
              </button>
              <button type="button" onClick={() => setPanel("none")}>
                {t("取消")}
              </button>
            </div>
          </form>
        )}
        {panel === "dismiss" && onDismiss && (
          <form
            className="event-manage-panel danger form-stack"
            onSubmit={(ev) => {
              ev.preventDefault();
              void onDismiss({
                disposition,
                reason: dismissReason.trim(),
                add_exclusion: addExclusion,
              });
            }}
          >
            <div className="action-row">
              <label className="check-row">
                <input
                  type="radio"
                  name="dismiss-disposition"
                  checked={disposition === "irrelevant"}
                  onChange={() => setDisposition("irrelevant")}
                />
                {t("不相关 / 误报")}
              </label>
              <label className="check-row">
                <input
                  type="radio"
                  name="dismiss-disposition"
                  checked={disposition === "unwanted"}
                  onChange={() => setDisposition("unwanted")}
                />
                {t("不想继续追踪")}
              </label>
            </div>
            <label>
              {t("原因说明（将作为长期排除规则反馈给后续检查）")}
              <input
                maxLength={160}
                value={dismissReason}
                onChange={(ev) => setDismissReason(ev.target.value)}
                placeholder={t("例如：排除无畏契约分部，仅追踪英雄联盟 LPL 分部")}
              />
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={addExclusion}
                onChange={(ev) => setAddExclusion(ev.target.checked)}
              />
              {t("沉淀为该主题的长期排除规则（可在编辑目标中随时撤回）；原新闻会保留为已排除，下次检查不重复建档")}
            </label>
            <div className="action-row">
              <button className="danger-button" disabled={disabled}>
                {t("确认移除事件")}
              </button>
              <button type="button" onClick={() => setPanel("none")}>
                {t("取消")}
              </button>
            </div>
          </form>
        )}
      </div>
      <EventTimeline value={value} onEvidence={onEvidence} />
      <div className="event-detail-grid">
        <div>
          <section className="detail-section">
            <h3>
              {t("当前事实")} <span className="count">{e.state.key_facts.length}</span>
            </h3>
            {!newestFactsFirst.length && <p className="hint">{t("暂时没有提炼出的事实。")}</p>}
            <ul className="fact-list">
              {newestFactsFirst.map((f, i) => {
                const isNew = latestAddedFactSet.has(JSON.stringify(f));
                return (
                  <li key={i} className={isNew ? "fact-new" : ""}>
                    <span>
                      {isNew && <span className="new-pill">{t("本轮新增")}</span>}
                      {tr(text(f.fact))}
                    </span>
                    <span className="citation-group">
                      {f.evidence_ids.map((id) => (
                        <button
                          className="citation"
                          key={id}
                          onClick={() => onEvidence(id)}
                          aria-label={t("查看事实 {0} 的证据", i + 1)}
                        >
                          {e.state.sources.includes(id) ? e.state.sources.indexOf(id) + 1 : t("依据")}
                        </button>
                      ))}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>
          {!!value.history.length && <details className="detail-section audit-history">
            <summary>{t("状态修改记录")} <span className="count">{value.history.length}</span></summary>
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
                      {h.before_title && h.after_title ? (
                        <>
                          {t("标题变更：")}
                          {h.before_title} <span className="muted">→</span> {h.after_title}
                        </>
                      ) : (
                        <>
                          {status(h.before_state?.current_status ?? t("新事件"))}{" "}
                          <span className="muted">→</span>{" "}
                          {status(h.after_state.current_status)}
                        </>
                      )}
                    </span>
                    <time>{date(h.committed_at)}</time>
                  </summary>
                  {h.impact_reason && <p className="hint">{tr(h.impact_reason)}</p>}
                  {added.map((f, i) => (
                    <p className="diff-added" key={i}>
                      ＋ {tr(text(f.fact))}
                    </p>
                  ))}
                  {removed.map((f, i) => (
                    <p className="diff-removed" key={i}>
                      − {tr(text(f.fact))}
                    </p>
                  ))}
                  <details className="quiet-details">
                    <summary>{t("完整修改记录")}</summary>
                    <pre>{pretty(h)}</pre>
                  </details>
                </details>
              );
            })}
          </details>}
        </div>
        <aside className="evidence-rail">
          <h3>
            {t("关联证据")} <span className="count">{value.evidence.length}</span>
          </h3>
          <p className="hint">
            {t("选择标题查看原文。事实旁的数字对应新闻编号。")}
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
                <strong>{tr(headline(ev.title))}</strong>
                <small>
                  {ev.source_id} · {date(ev.published_at, false)}
                </small>
                <span
                  className={`tag ${ev.relation === "conflicting" ? "amber" : ""}`}
                >
                  {{
                    supporting: t("支持证据"),
                    conflicting: t("冲突证据"),
                    duplicate: t("重复报道"),
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
      <ReferenceTranslation key={e.evidence_id} title={e.title} body={e.content} />
      <p className="article-body">{text(e.content)}</p>
      {url && (
        <a
          className="external-link"
          href={url}
          target="_blank"
          rel="noreferrer"
        >
          {t("原始链接")} <Icon name="external" size={14} />
        </a>
      )}
      <dl className="metadata-list">
        <div>
          <dt>{t("发布时间")}</dt>
          <dd>{date(e.published_at)}</dd>
        </div>
        <div>
          <dt>{t("获取时间")}</dt>
          <dd>{date(e.fetched_at)}</dd>
        </div>
        <div>
          <dt>{t("当前处理")}</dt>
          <dd>
            {value.status === "review"
              ? t("待审核")
              : value.status === "failed"
                ? t("处理失败")
                : value.status === "failed_retryable"
                  ? t("服务故障，可重试")
                  : value.status === "pending"
                    ? t("未处理")
                    : a?.is_relevant === false
                      ? t("已排除")
                      : e.canonical_evidence_id !== e.evidence_id && !a
                        ? t("重复报道")
                        : t("已处理")}
          </dd>
        </div>
      </dl>
      {value.error != null && (
        <div role="alert" className="error">
          {typeof value.error === "object" && "message" in value.error
            ? String(value.error.message)
            : t("处理失败，请查看运行记录。")}
        </div>
      )}
      {a && (
        <div className="assessment-note">
          <h3>{t("为什么这样处理")}</h3>
          <p>{a.is_relevant ? t("与追踪目标相关。") : t("判断为与追踪目标无关。")}</p>
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
        <summary>{t("原始内容与技术记录")}</summary>
        <p className="raw-text">{e.content}</p>
        <pre>{pretty(value)}</pre>
      </details>
    </article>
  );
}
