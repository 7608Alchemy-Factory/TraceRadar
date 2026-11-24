import { MAX_TOPIC_TAGS, TopicTagPicker } from "./topic-tags";
import { t } from "./i18n";
import React, { useEffect, useState } from "react";
import { api, json } from "./api";
import type {
  Topic,
  Source,
  Review,
  ReviewResolution,
  Event,
  ModelStatus,
  SearchStatus,
  Corpus,
} from "./types";
import { date, text, title as eventTitle, headline } from "./display";
import { searchProviderLabel } from "./source-health";
import { Icon, Empty } from "./ui";
export type Act = (task: () => Promise<void>) => Promise<void>;

export function GoalEditor({
  topic,
  availableTags,
  disabled,
  action,
  onSaved,
}: {
  topic: Topic;
  availableTags: string[];
  disabled: boolean;
  action: Act;
  onSaved: () => Promise<void>;
}) {
  const [title, setTitle] = useState(topic.title),
    [description, setDescription] = useState(topic.description);
  const [tags, setTags] = useState(topic.tags ?? []);
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        void action(async () => {
          await api(
            `/topics/${topic.topic_id}`,
            json("PATCH", { title, description, tags }),
          );
          await onSaved();
        });
      }}
    >
      <label>
        {t("主题名称")}
        <input
          required
          maxLength={200}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label>
        {t("追踪目标")}
        <textarea
          required
          aria-label={t("追踪目标")}
          maxLength={4000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
      </label>
      <TopicTagPicker value={tags} options={availableTags} onChange={setTags} />
      <p className="hint">
        {t("新目标用于后续检查。已有状态和历史保留；需要从头重跑时，可创建新主题。")}
      </p>
      <button className="primary" disabled={disabled || tags.length > MAX_TOPIC_TAGS}>
        {t("保存追踪目标")} <Icon name="arrow" size={16} />
      </button>
    </form>
  );
}

export function RegisterSource({
  disabled,
  action,
  onSaved,
  knownIds,
}: {
  disabled: boolean;
  action: Act;
  onSaved: () => Promise<void>;
  knownIds: string[];
}) {
  const [kind, setKind] = useState("web_search"),
    [id, setId] = useState("ws_custom"),
    [endpoint, setEndpoint] = useState(""),
    [searchQuery, setSearchQuery] = useState(""),
    [fetchFullText, setFetchFullText] = useState(true);
  const handleKindChange = (next: string) => {
    setKind(next);
    const defaults: Record<string, string> = {
      web_search: "ws_custom",
      webpage: "page_monitor",
      rss: "my_rss",
      manual: "my_news",
      lbj: "lbj_replay",
      nba: "nba_replay",
    };
    if (Object.values(defaults).includes(id)) {
      setId(defaults[next] ?? "my_source");
    }
  };
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        void action(async () => {
          if (knownIds.includes(id))
            throw Error(t("来源 ID 已存在，请换一个名称。"));
          const type =
            kind === "web_search"
              ? "web_search"
              : kind === "webpage"
                ? "webpage"
                : kind === "rss"
                  ? "rss"
                  : kind === "manual"
                    ? "manual"
                    : "replay";
          const path =
            kind === "lbj"
              ? "lbj_2026_extended/all.json"
              : "nba_2026_multi_event/all.json";
          const cleanQuery = searchQuery.trim();
          await api(
            "/sources",
            json("POST", {
              source_id: id,
              name:
                kind === "web_search"
                  ? `${t("全网搜索")} · ${cleanQuery.slice(0, 36)}`
                  : kind === "webpage"
                    ? `${t("网页监控")} · ${new URL(endpoint).hostname}`
                    : kind === "manual"
                      ? t("手动录入")
                      : kind === "rss"
                        ? t("RSS 订阅")
                        : t("{0} 回放", kind.toUpperCase()),
              type,
              endpoint:
                type === "web_search"
                  ? `search://${encodeURIComponent(cleanQuery).slice(0, 800)}`
                  : type === "webpage" || type === "rss"
                    ? endpoint.trim()
                    : type === "manual"
                      ? "manual://notes"
                      : `replay://${path}`,
              config:
                type === "web_search"
                  ? {
                      query: cleanQuery,
                      max_items: 8,
                      fetch_full_text: fetchFullText,
                      role: "branch",
                      branch_label: cleanQuery.slice(0, 40),
                    }
                  : type === "webpage"
                    ? { role: "channel" }
                    : type === "replay"
                      ? { corpus_path: path }
                      : type === "rss"
                        ? {
                            max_items: 10,
                            fetch_full_text: fetchFullText,
                            role: "channel",
                          }
                        : {},
            }),
          );
          await onSaved();
        });
      }}
    >
      <label>
        {t("来源类型")}
        <select value={kind} onChange={(e) => handleKindChange(e.target.value)}>
          <option value="web_search">{t("全网搜索")}</option>
          <option value="webpage">{t("网页监控")}</option>
          <option value="rss">{t("RSS 订阅")}</option>
          <option value="manual">{t("手动录入新闻")}</option>
          <option value="lbj">{t("LBJ 离线语料 · 30 条")}</option>
          <option value="nba">{t("NBA 离线语料 · 18 条")}</option>
        </select>
      </label>
      <label>
        {t("来源标识")}
        <input
          required
          pattern="[a-z0-9][a-z0-9_-]{1,63}"
          value={id}
          onChange={(e) => setId(e.target.value)}
        />
        <small>{t("使用小写字母、数字和下划线，如 my_news。")}</small>
      </label>
      {kind === "web_search" && (
        <>
          <label>
            {t("搜索关键词")}
            <input
              required
              maxLength={200}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t("例如 香港岛铁路规划")}
            />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={fetchFullText}
              onChange={(e) => setFetchFullText(e.target.checked)}
            />
            {t("优先读取新文章全文，失败时使用摘要")}
          </label>
        </>
      )}
      {kind === "webpage" && (
        <label>
          {t("目标网页地址")}
          <input
            type="url"
            required
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="https://example.org/news"
          />
          <small>
            {t("每次检查对比上次网页快照。有变化时才记录更新。")}
          </small>
        </label>
      )}
      {kind === "rss" && (
        <>
          <label>
            {t("订阅地址")}
            <input
              type="url"
              required
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              placeholder="https://…/feed.xml"
            />
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={fetchFullText}
              onChange={(e) => setFetchFullText(e.target.checked)}
            />
            {t("优先读取新文章全文，失败时使用摘要")}
          </label>
        </>
      )}
      <p className="hint">{t("添加后，在来源列表勾选并保存，才会用于这个主题。")}</p>
      <button className="primary" disabled={disabled}>
        {t("添加来源")} <Icon name="plus" size={16} />
      </button>
    </form>
  );
}

export function ManualEvidenceForm({
  sources,
  disabled,
  submit,
}: {
  sources: Source[];
  disabled: boolean;
  submit: (item: Record<string, string>) => Promise<void>;
}) {
  const [sourceId, setSourceId] = useState(""),
    [title, setTitle] = useState(""),
    [content, setContent] = useState(""),
    [url, setUrl] = useState(""),
    [published, setPublished] = useState("");
  const approved = sources.filter((s) => s.type === "manual");
  if (!approved.length)
    return (
      <Empty
        title={t("先批准一个手动来源")}
        detail={t("在「信息源」中添加手动录入来源，勾选并保存后，即可提交新闻。")}
      />
    );
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        void submit({
          source_id: approved.some((s) => s.source_id === sourceId)
            ? sourceId
            : approved[0].source_id,
          title,
          content,
          url,
          published_at: new Date(published).toISOString(),
        });
      }}
    >
      <label>
        {t("录入来源")}
        <select
          value={
            approved.some((s) => s.source_id === sourceId)
              ? sourceId
              : approved[0].source_id
          }
          onChange={(e) => setSourceId(e.target.value)}
        >
          {approved.map((s) => (
            <option key={s.source_id} value={s.source_id}>
              {s.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("新闻标题")}
        <input
          required
          maxLength={500}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </label>
      <label>
        {t("原文内容")}
        <textarea
          required
          rows={7}
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </label>
      <div className="form-grid">
        <label>
          {t("原文链接")}
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={t("可选")}
          />
        </label>
        <label>
          {t("发布时间 · 本地时间")}
          <input
            required
            type="datetime-local"
            value={published}
            onChange={(e) => setPublished(e.target.value)}
          />
        </label>
      </div>
      <p className="hint">
        {t("使用当前选择的分析方式检查全部已批准来源。原文与发布时间会保留，供后续追溯。")}
      </p>
      <button className="primary" disabled={disabled}>
        {t("提交并分析")} <Icon name="arrow" size={16} />
      </button>
    </form>
  );
}

function ReviewItem({
  review,
  events,
  topicId,
  disabled,
  action,
  onSaved,
}: {
  review: Review;
  events: Event[];
  topicId: string;
  disabled: boolean;
  action: Act;
  onSaved: () => Promise<void>;
}) {
  const [mode, setMode] = useState<"dismiss" | "accept">("dismiss");
  const [note, setNote] = useState(""),
    [eventId, setEventId] = useState(
      review.assessment.association.event_id ?? events[0]?.event_id ?? "",
    );
  const [selected, setSelected] = useState(
    events.find((e) => e.event_id === eventId),
  );
  const stale =
    events.find((e) => e.event_id === eventId)?.version !== selected?.version;
  const [fact, setFact] = useState(""),
    [currentStatus, setCurrentStatus] = useState(
      selected?.state.current_status ?? "",
    ),
    [removed, setRemoved] = useState<number[]>([]),
    [major, setMajor] = useState(false),
    [confirmed, setConfirmed] = useState(false);
  return (
    <article className="review-item">
      <div className="row-between">
        <span className="tag amber">
          {review.assessment.novelty === "conflict" ? t("信息冲突") : t("归属待定")}
        </span>
        <span className="meta">{date(review.evidence.published_at)}</span>
      </div>
      <h3>{headline(review.evidence.title)}</h3>
      <p className="review-content">{text(review.evidence.content)}</p>
      <details className="quiet-details">
        <summary>{t("查看原始判断与证据")}</summary>
        {(review.event_decisions ?? [review.assessment]).map((decision, i) => (
          <div key={i}>
            <p>{decision.association.reason}</p>
            {decision.conflicts.map((conflict, j) => <p key={j}>{text(conflict)}</p>)}
          </div>
        ))}
        <p className="raw-text">{review.evidence.content}</p>
        <small>{review.evidence.url}</small>
      </details>
      <div className="segmented">
        <button
          type="button"
          className={mode === "dismiss" ? "chosen" : ""}
          onClick={() => setMode("dismiss")}
        >
          {t("保留原状态")}
        </button>
        <button
          type="button"
          className={mode === "accept" ? "chosen" : ""}
          disabled={!events.length}
          onClick={() => setMode("accept")}
        >
          {t("采纳并修正")}
        </button>
      </div>
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          void action(async () => {
            if (mode === "accept" && !confirmed)
              throw Error(t("请先确认已核对这条证据。"));
            await api(
              `/topics/${topicId}/reviews/${review.evidence.evidence_id}/resolve`,
              json(
                "POST",
                mode === "dismiss"
                  ? { decision: "dismiss" }
                  : {
                      decision: "accept",
                      note,
                      event_id: eventId,
                      expected_version: selected!.version,
                      current_status: currentStatus,
                      fact,
                      superseded_facts: removed.map(
                        (i) => selected!.state.key_facts[i],
                      ),
                      update_kind: major ? "major_milestone" : "fact_added",
                    },
              ),
            );
            await onSaved();
          });
        }}
      >
        {mode === "accept" && selected && (
          <>
            <label>
              {t("关联事件")}
              <select
                value={eventId}
                onChange={(e) => {
                  const next = events.find(
                    (v) => v.event_id === e.target.value,
                  )!;
                  setEventId(e.target.value);
                  setSelected(next);
                  setCurrentStatus(next.state.current_status);
                  setRemoved([]);
                  setConfirmed(false);
                }}
              >
                {events.map((v) => (
                  <option key={v.event_id} value={v.event_id}>
                    {eventTitle(v.title)}
                  </option>
                ))}
              </select>
            </label>
            {stale && (
              <div className="error">
                {t("事件在你审核时已有新版本。")}
                <button
                  type="button"
                  onClick={() => {
                    const next = events.find((v) => v.event_id === eventId)!;
                    setSelected(next);
                    setCurrentStatus(next.state.current_status);
                    setRemoved([]);
                    setConfirmed(false);
                  }}
                >
                  {t("重新载入当前状态")}
                </button>
              </div>
            )}
            <label>
              {t("采纳后的事实")}
              <textarea
                required
                value={fact}
                onChange={(e) => setFact(e.target.value)}
                placeholder={t("写明核实后的事实；系统会引用当前这条证据。")}
              />
            </label>
            <label>
              {t("事件当前状态")}
              <input
                required
                value={currentStatus}
                onChange={(e) => setCurrentStatus(e.target.value)}
              />
            </label>
            <fieldset>
              <legend>{t("需要替换的旧事实（可选）")}</legend>
              {selected.state.key_facts.map((f, i) => (
                <label className="check-row" key={i}>
                  <input
                    type="checkbox"
                    checked={removed.includes(i)}
                    onChange={(e) =>
                      setRemoved(
                        e.target.checked
                          ? [...removed, i]
                          : removed.filter((v) => v !== i),
                      )
                    }
                  />
                  <span>{text(f.fact)}</span>
                </label>
              ))}
              <p className="hint">
                {t("旧事实将从当前事实中移出，但完整保留在状态历史中。")}
              </p>
            </fieldset>
            <label className="check-row">
              <input
                type="checkbox"
                checked={major}
                onChange={(e) => setMajor(e.target.checked)}
              />
              <span>{t("属于重大进展，按主题偏好提醒（需改变当前状态）")}</span>
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                required
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>{t("我已核对证据，确认上述修改。")}</span>
            </label>
          </>
        )}
        {mode === "accept" && <label>
          {t("处理依据")}
          <textarea
            required
            maxLength={2000}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("为什么采纳这条证据？")}
          />
        </label>}
        <button
          className={mode === "accept" ? "primary" : ""}
          disabled={disabled || (mode === "accept" && (!note.trim() || !selected || stale))}
        >
          {mode === "accept" ? t("采纳并更新事件") : t("保留原状态并关闭")}{" "}
          <Icon name="check" size={16} />
        </button>
      </form>
    </article>
  );
}

export function ReviewPanel({
  topicId,
  reviews,
  resolutions,
  events,
  disabled,
  action,
  onSaved,
}: {
  topicId: string;
  reviews: Review[];
  resolutions: ReviewResolution[];
  events: Event[];
  disabled: boolean;
  action: Act;
  onSaved: () => Promise<void>;
}) {
  return (
    <div className="review-layout">
      <div>
        {reviews.length ? (
          reviews.map((r) => (
            <ReviewItem
              key={r.evidence.evidence_id}
              review={r}
              events={events}
              topicId={topicId}
              disabled={disabled}
              action={action}
              onSaved={onSaved}
            />
          ))
        ) : (
          <Empty
            title={t("当前没有待审信息")}
            detail={t("相互矛盾的报道、无法确定归属的信息，会保留在这里，等待你判断。")}
          />
        )}
      </div>
      <aside className="context-panel">
        <span className="kicker">REVIEW HISTORY</span>
        <h3>{t("人工处理记录")}</h3>
        <p className="hint">
          {t("审核决定会留在记录里。采纳后的状态变化会出现在事件历史和运行记录中。")}
        </p>
        {!resolutions.length && <p className="meta">{t("尚无处理记录")}</p>}
        {resolutions.map((r) => (
          <div className="audit-row" key={r.resolution_id}>
            <span className={`tag ${r.decision === "accept" ? "green" : ""}`}>
              {r.decision === "accept" ? t("已采纳") : t("保留原状态")}
            </span>
            {r.note && <p>{r.note}</p>}
            <small>{date(r.resolved_at)} {t("· 本地用户")}</small>
          </div>
        ))}
      </aside>
    </div>
  );
}

export function ModelSettingsPanel({
  value,
  disabled,
  action,
  onSaved,
}: {
  value: ModelStatus | null;
  disabled: boolean;
  action: Act;
  onSaved: (s: ModelStatus) => void;
}) {
  const [address, setAddress] = useState(value?.base_url ?? ""),
    [model, setModel] = useState(value?.model ?? ""),
    [key, setKey] = useState(""),
    [message, setMessage] = useState("");
  const [available, setAvailable] = useState<{ id: string; name: string }[]>(
    [],
  );
  return (
    <div>
      <div className="connection-note">
        <span className={`status-dot ${value?.configured ? "online" : ""}`} />
        <div>
          <strong>
            {value?.configured ? t("配置已保存") : t("尚未配置真实模型")}
          </strong>
          <p>{t("规则演示不需要模型。分析 LBJ、NBA 或其他主题时，需要真实模型。")}</p>
        </div>
      </div>
      <ol className="config-instructions">
        <li>{t("确定团队使用的模型服务，在其控制台创建 API Key。")}</li>
        <li>
          {t("复制该服务提供的兼容 API 基础地址，结尾不要加 /chat/completions。")}
        </li>
        <li>
          {t("填写可调用的模型 ID，保存后点击测试连接。尚未确定服务时，可以先完成模拟演示和回放准备。")}
        </li>
      </ol>
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          setMessage("");
          void action(async () => {
            const result = await api<ModelStatus>(
              "/settings/model",
              json("PUT", { base_url: address, model, api_key: key || null }),
            );
            setKey("");
            onSaved(result);
            setMessage(t("配置已保存。可以测试连接，或开始分析。"));
          });
        }}
      >
        <label>
          {t("API 基础地址")}
          <input
            required
            type="url"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            placeholder={t("团队提供的 HTTPS 兼容接口地址")}
          />
        </label>
        <label>
          {t("模型名称")}
          <input
            required
            list="available-model-ids"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder={t("填写服务端实际支持的模型名")}
          />
          <datalist id="available-model-ids">
            {available.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </datalist>
        </label>
        <div>
          <button
            type="button"
            disabled={disabled || !address || (!key && !value?.key_configured)}
            onClick={() =>
              void action(async () => {
                const result = await api<{
                  models: { id: string; name: string }[];
                }>(
                  "/settings/model/models",
                  json("POST", { base_url: address, api_key: key || null }),
                );
                setAvailable(result.models);
                setMessage(
                  t("已读取 {0} 个模型。请选择实际 ID，再保存配置。", result.models.length),
                );
              })
            }
          >
            {t("读取可用模型")}
          </button>
          <div className="button-row">
            {available.map((m) => (
              <button type="button" key={m.id} onClick={() => setModel(m.id)}>
                {m.id}
              </button>
            ))}
          </div>
        </div>
        <label>
          API Key
          <input
            type="password"
            autoComplete="new-password"
            required={!value?.key_configured}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={
              value?.key_configured
                ? t("已保存；留空保持原 Key")
                : t("仅保存至本机后端配置")
            }
          />
        </label>
        <p className="hint">
          {t("配置写入本机被 Git 忽略的 .env。Key 不会回显到页面，也不会进入事件与运行记录。")}
        </p>
        <div className="button-row">
          <button className="primary" disabled={disabled}>
            {t("保存配置")}
          </button>
          <button
            type="button"
            disabled={disabled || !value?.configured}
            onClick={() =>
              void action(async () => {
                setMessage(t("正在连接模型…"));
                try {
                  const result = await api<{ status: string; model: string }>(
                    "/settings/model/check",
                    json("POST", {}),
                  );
                  setMessage(t("连接成功 · {0}", result.model));
                } catch (e) {
                  setMessage(t("连接失败，请检查上方错误信息。"));
                  throw e;
                }
              })
            }
          >
            {t("测试连接 · 一次模型请求")}
          </button>
        </div>
        {message && (
          <p role="status" className="inline-status">
            {message}
          </p>
        )}
      </form>
    </div>
  );
}

export function SearchSettingsPanel({
  value,
  disabled,
  action,
  onSaved,
}: {
  value: SearchStatus | null;
  disabled: boolean;
  action: Act;
  onSaved: (next: SearchStatus) => Promise<void> | void;
}) {
  const [provider, setProvider] = useState<SearchStatus["provider"]>(
    value?.provider ?? "auto",
  );
  const [bochaKey, setBochaKey] = useState("");
  const [tavilyKey, setTavilyKey] = useState("");
  const [jinaFallback, setJinaFallback] = useState(
    value?.web_fetch_jina_fallback ?? true,
  );
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (value) {
      setProvider(value.provider);
      setJinaFallback(value.web_fetch_jina_fallback);
    }
  }, [value]);

  return (
    <div className="form-stack">
      <div className="settings-summary">
        <strong>{t("当前搜索引擎")} · {value ? searchProviderLabel(value.active_provider) : t("状态未知")}</strong>
        <p className="hint">{t("API 密钥由服务器保存。已配置的密钥留空即可保留。")}</p>
      </div>
      <form
        className="form-stack"
        onSubmit={(e) => {
          e.preventDefault();
          setMessage("");
          void action(async () => {
            const payload: Record<string, unknown> = {
              provider,
              web_fetch_jina_fallback: jinaFallback,
            };
            if (bochaKey.trim()) payload.bocha_api_key = bochaKey.trim();
            if (tavilyKey.trim()) payload.tavily_api_key = tavilyKey.trim();
            const saved = await api<SearchStatus>(
              "/settings/search",
              json("PUT", payload),
            );
            setBochaKey("");
            setTavilyKey("");
            setMessage(t("搜索配置已保存。"));
            await onSaved(saved);
          });
        }}
      >
        <label>
          {t("搜索路由模式")}
          <select
            value={provider}
            onChange={(e) =>
              setProvider(e.target.value as SearchStatus["provider"])
            }
          >
            <option value="auto">{t("自动按语种选择搜索引擎")}</option>
            <option value="bocha">{t("博查 API")}</option>
            <option value="tavily">Tavily</option>
            <option value="free">{t("免密钥模式")} · Bing / Google News</option>
          </select>
        </label>
        <label>
          {t("博查 API 密钥")}
          <input
            type="password"
            value={bochaKey}
            onChange={(e) => setBochaKey(e.target.value)}
            placeholder={value?.bocha_configured ? t("已配置，留空保留") : "sk-…"}
          />
        </label>
        <label>
          {t("Tavily API 密钥")}
          <input
            type="password"
            value={tavilyKey}
            onChange={(e) => setTavilyKey(e.target.value)}
            placeholder={value?.tavily_configured ? t("已配置，留空保留") : "tvly-…"}
          />
        </label>
        <label className="check-row">
          <input
            type="checkbox"
            checked={jinaFallback}
            onChange={(e) => setJinaFallback(e.target.checked)}
          />
          {t("无法直接读取公开网页时，尝试使用 Jina Reader")}
        </label>
        <div className="action-row">
          <button className="primary" disabled={disabled}>
            {t("保存搜索配置")}
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() =>
              void action(async () => {
                setMessage(t("正在测试搜索连接…"));
                try {
                  const res = await api<{
                    provider_used: string;
                    items: number;
                  }>("/settings/search/check", json("POST", {}));
                  setMessage(
                    t("连接成功，{0} 返回 {1} 条结果。", searchProviderLabel(res.provider_used), res.items),
                  );
                } catch (err) {
                  setMessage(t("连接失败，请检查上方错误信息。"));
                  throw err;
                }
              })
            }
          >
            {t("测试已保存的引擎")}
          </button>
        </div>
        {message && (
          <p role="status" className="inline-status">
            {message}
          </p>
        )}
      </form>
    </div>
  );
}

export function ReplaySetup({
  corpora,
  disabled,
  action,
  onPrepared,
}: {
  corpora: Corpus[];
  disabled: boolean;
  action: Act;
  onPrepared: (topic: Topic, corpus: Corpus) => Promise<void>;
}) {
  const defaultGoalFor = (corpus?: Corpus) =>
    corpus?.default_goal ||
    "持续追踪该主题的重要进展，区分传闻、确认进展与冲突证据；同一事项的后续更新关联原事件，独立事项分开建档。";
  const [id, setId] = useState(corpora[0]?.corpus_id ?? ""),
    [goal, setGoal] = useState(defaultGoalFor(corpora[0]));
  useEffect(() => {
    if (!id && corpora[0]) {
      setId(corpora[0].corpus_id);
      setGoal(defaultGoalFor(corpora[0]));
    }
  }, [id, corpora]);
  const selected = corpora.find((c) => c.corpus_id === id);
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        if (!selected) return;
        void action(async () => {
          const sourceId = `anchor_${id}_${crypto.randomUUID().slice(0, 8)}`;
          const anchorConfig: Record<string, unknown> = {
            corpus_path: selected.corpus_path,
            role: "anchor",
            ...(selected.anchor_terms?.length
              ? { query_any: selected.anchor_terms }
              : {}),
          };
          await api(
            "/sources",
            json("POST", {
              source_id: sourceId,
              name: t("{0} · 主检索源", selected.name),
              type: "replay",
              endpoint: `replay://${selected.corpus_path}`,
              config: anchorConfig,
            }),
          );
          const topic = await api<Topic>(
            "/topics",
            json("POST", { title: selected.name, description: goal }),
          );
          await api(
            `/topics/${topic.topic_id}/sources`,
            json("PUT", { approved_source_ids: [sourceId] }),
          );
          await onPrepared(topic, selected);
        });
      }}
    >
      <div className="corpus-options">
        {corpora.map((c) => (
          <label
            className={`corpus-option ${id === c.corpus_id ? "chosen" : ""}`}
            key={c.corpus_id}
          >
            <input
              type="radio"
              name="corpus"
              checked={id === c.corpus_id}
              onChange={() => {
                setId(c.corpus_id);
                setGoal(defaultGoalFor(c));
              }}
            />
            <span>
              <strong>{c.name}</strong>
              <small>
                {c.records} {t("条新闻 ·")} {c.cutoffs.length} {t("个时间切片")}
              </small>
            </span>
          </label>
        ))}
      </div>
      <label>
        {t("追踪目标")}
        <textarea
          required
          aria-label={t("回放追踪目标")}
          rows={4}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
      </label>
      <p className="hint">
        {t("使用队友提供的离线语料，创建独立主题并批准回放来源。此操作不调用模型。新闻真实性与预期标签仍需核验。")}
      </p>
      <button className="primary" disabled={disabled || !selected}>
        {t("准备回放工作区")} <Icon name="arrow" size={16} />
      </button>
    </form>
  );
}
