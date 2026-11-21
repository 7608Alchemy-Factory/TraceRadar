import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, json } from "./api";
import type {
  Topic,
  Source,
  Dashboard,
  Notification,
  RunDetail,
  EventDetail,
  EvidenceDecision,
  ModelStatus,
  Corpus,
} from "./types";
import {
  GoalEditor,
  RegisterSource,
  ManualEvidenceForm,
  ReviewPanel,
  ModelSettingsPanel,
  ReplaySetup,
} from "./member-d";
import { Icon, Modal, Empty } from "./ui";
import { EventView, EvidenceView } from "./event-view";
import {
  active,
  pretty,
  date,
  status,
  title as eventTitle,
  provider as providerLabel,
  actionLabel,
  stepLabel,
  text,
  headline,
  localInput,
} from "./display";
import "./style.css";

const tabs = [
  ["overview", "概览", "grid"],
  ["events", "事件", "events"],
  ["evidence", "证据库", "evidence"],
  ["review", "待审核", "review"],
  ["sources", "信息源", "sources"],
  ["activity", "运行记录", "activity"],
] as const;
type Tab = (typeof tabs)[number][0];
type Dialog =
  | "new"
  | "goal"
  | "source"
  | "manual"
  | "settings"
  | "replay"
  | "help"
  | null;
const demoSteps = [
  {
    id: "1",
    name: "发现事件",
    description: "披露漏洞 A；无关体育新闻被排除。",
  },
  {
    id: "2",
    name: "补充与归类",
    description: "更新 A 的受影响版本；转载去重；独立漏洞 B 单独建档。",
  },
  {
    id: "3",
    name: "修复与冲突",
    description: "补丁使 A 变为已修复并提醒；矛盾报道留待审核。",
  },
];

function App() {
  const [topics, setTopics] = useState<Topic[]>([]),
    [selected, setSelected] = useState(""),
    [tab, setTab] = useState<Tab>("overview");
  const [dashboard, setDashboard] = useState<Dashboard | null>(null),
    [sources, setSources] = useState<Source[]>([]),
    [approved, setApproved] = useState<string[]>([]);
  const [evidence, setEvidence] = useState<EvidenceDecision[]>([]),
    [notices, setNotices] = useState<Notification[]>([]),
    [run, setRun] = useState<RunDetail | null>(null);
  const [event, setEvent] = useState<EventDetail | null>(null),
    [evidenceId, setEvidenceId] = useState(""),
    [model, setModel] = useState<ModelStatus | null>(null),
    [corpora, setCorpora] = useState<Corpus[]>([]);
  const [dialog, setDialog] = useState<Dialog>(null),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [toast, setToast] = useState("");
  const [health, setHealth] = useState(false),
    [query, setQuery] = useState(""),
    [watchId, setWatchId] = useState("");
  const [provider, setProvider] = useState<"mock" | "qwen">("mock"),
    [batch, setBatch] = useState("1"),
    [asOf, setAsOf] = useState("");
  const [replayCorpus, setReplayCorpus] = useState("lbj");
  const [showHistorySources, setShowHistorySources] = useState(false);
  const [newTitle, setNewTitle] = useState(""),
    [newDescription, setNewDescription] = useState("");
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const running = active(dashboard?.recent_runs[0]?.status);
  const replay = dashboard?.approved_sources.some((s) => s.type === "replay");
  const fixture =
    !replay && dashboard?.approved_sources.some((s) => s.type === "fixture");
  const replayCorpora = corpora.filter((c) =>
    dashboard?.approved_sources.some(
      (s) => s.type === "replay" && s.config.corpus_path === c.corpus_path,
    ),
  );
  const corpus =
    replayCorpora.find((c) => c.corpus_id === replayCorpus) ?? replayCorpora[0];
  const replaySource = dashboard?.approved_sources.find(
    (s) => s.type === "replay" && s.config.corpus_path === corpus?.corpus_path,
  );
  const currentEvidence = evidence.filter((v) =>
    replaySource
      ? v.evidence.source_id === replaySource.source_id
      : dashboard?.approved_sources.some(
          (s) => s.source_id === v.evidence.source_id,
        ),
  );

  function selectTopic(id: string) {
    if (id === selectedRef.current) {
      setTab("overview");
      return;
    }
    selectedRef.current = id;
    setSelected(id);
    setDashboard(null);
    setLoading(!!id);
    setRun(null);
    setEvidence([]);
    setSources([]);
    localStorage.setItem("traceradar-topic", id);
    const address = new URL(location.href);
    if (id) address.searchParams.set("topic", id);
    else address.searchParams.delete("topic");
    history.replaceState({}, "", address);
    setWatchId("");
    setEvent(null);
    setEvidenceId("");
    setQuery("");
    setShowHistorySources(false);
    setTab("overview");
    setError("");
  }
  useEffect(() => {
    void Promise.all([
      api<Topic[]>("/topics"),
      api<{ status: string }>("/health"),
      api<ModelStatus>("/settings/model"),
      api<Corpus[]>("/replay/corpora"),
    ])
      .then(([items, h, m, c]) => {
        setTopics(items);
        setHealth(h.status === "ok");
        setModel(m);
        setCorpora(c);
        const previous = localStorage.getItem("traceradar-topic");
        const requested = new URLSearchParams(location.search).get("topic");
        selectTopic(
          items.find((t) => t.topic_id === requested)?.topic_id ??
            items.find((t) => t.topic_id === previous)?.topic_id ??
            [...items].reverse().find((t) => t.title.includes("三步演示"))
              ?.topic_id ??
            items.find((t) => t.title.includes("D验收"))?.topic_id ??
            items.at(-1)?.topic_id ??
            "",
        );
        if (!items.length) setLoading(false);
      })
      .catch((e) => {
        setError(String(e));
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!selected) return;
    let disposed = false;
    setLoading(true);
    setDashboard(null);
    setRun(null);
    void Promise.all([
      api<Dashboard>(`/topics/${selected}/dashboard`),
      api<Notification[]>(`/topics/${selected}/notifications`),
      api<EvidenceDecision[]>(`/topics/${selected}/evidence`),
      api<{ sources: Source[] }>(
        `/topics/${selected}/source-recommendations`,
        json("POST", {}),
      ),
    ])
      .then(async ([d, n, e, s]) => {
        if (disposed || selectedRef.current !== selected) return;
        setDashboard(d);
        setNotices(n);
        setEvidence(e);
        setSources(s.sources);
        setApproved(d.approved_sources.map((s) => s.source_id));
        setBatch(
          d.approved_sources.some((s) => s.type === "replay") ? "all" : "1",
        );
        setProvider(
          !d.approved_sources.some((s) => s.type === "replay") &&
            d.approved_sources.some((s) => s.type === "fixture")
            ? "mock"
            : "qwen",
        );
        const availableCorpora = corpora.filter((c) =>
          d.approved_sources.some(
            (s) =>
              s.type === "replay" && s.config.corpus_path === c.corpus_path,
          ),
        );
        const preferred =
          /nba/i.test(d.topic.title) && !/lebron|lbj/i.test(d.topic.title)
            ? "nba"
            : "lbj";
        const selectedCorpus =
          availableCorpora.find((c) => c.corpus_id === preferred) ??
          availableCorpora[0];
        if (selectedCorpus) setReplayCorpus(selectedCorpus.corpus_id);
        const cutoff =
          d.recent_runs.find((r) => r.snapshot?.options?.as_of)?.snapshot
            .options?.as_of ?? selectedCorpus?.cutoffs[0];
        setAsOf(cutoff ? localInput(cutoff) : "");
        const latest = d.recent_runs[0];
        if (latest) {
          const detail = await api<RunDetail>(`/runs/${latest.run_id}`);
          if (disposed) return;
          setRun(detail);
          if (active(latest.status)) setWatchId(latest.run_id);
        }
      })
      .catch((e) => {
        if (!disposed) setError(String(e));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [selected]);

  async function refresh() {
    const id = selected;
    const [d, n, e] = await Promise.all([
      api<Dashboard>(`/topics/${id}/dashboard`),
      api<Notification[]>(`/topics/${id}/notifications`),
      api<EvidenceDecision[]>(`/topics/${id}/evidence`),
    ]);
    if (selectedRef.current !== id) return;
    setDashboard(d);
    setNotices(n);
    setEvidence(e);
  }
  async function refreshSources() {
    const s = await api<{ sources: Source[] }>(
      `/topics/${selected}/source-recommendations`,
      json("POST", {}),
    );
    setSources(s.sources);
  }
  async function action(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setToast("");
    try {
      await task();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!watchId || !selected) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const detail = await api<RunDetail>(`/runs/${watchId}`);
        if (disposed || detail.run.topic_id !== selected) return;
        setRun(detail);
        await refresh();
        if (!disposed && active(detail.run.status))
          timer = setTimeout(poll, 900);
      } catch (e) {
        if (!disposed) setError(String(e));
      }
    }
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [watchId, selected]);

  async function startRun(
    options: {
      batch?: string;
      provider?: "mock" | "qwen";
      asOf?: string;
      manualItems?: Record<string, string>[];
    } = {},
  ) {
    const engine = options.provider ?? provider,
      cutoff = options.asOf ?? asOf;
    if (engine === "qwen" && !model?.configured) {
      setDialog("settings");
      throw Error("请先填写模型服务配置。");
    }
    if (replay && !cutoff) throw Error("请选择回放截止时间。");
    const result = await api<{ run_id: string }>(
      `/topics/${selected}/runs`,
      json("POST", {
        fixture_batch_id: replay ? "all" : (options.batch ?? batch),
        ...(replaySource
          ? {
              source_ids: [
                ...new Set([
                  replaySource.source_id,
                  ...(options.manualItems ?? []).map((item) => item.source_id),
                ]),
              ],
            }
          : {}),
        provider: engine,
        ...(cutoff ? { as_of: new Date(cutoff).toISOString() } : {}),
        ...(options.manualItems ? { manual_items: options.manualItems } : {}),
      }),
    );
    setEvent(null);
    setWatchId(result.run_id);
    setDialog(null);
    await refresh();
  }
  async function openEvent(id: string) {
    await action(async () => setEvent(await api<EventDetail>(`/events/${id}`)));
  }
  async function resumeRun(id: string) {
    const result = await api<{ run_id: string }>(`/runs/${id}/resume`, json("POST", {}));
    setWatchId(result.run_id);
    await refresh();
  }
  function showDialog(d: Dialog) {
    setError("");
    setDialog(d);
  }
  async function createDemo() {
    await action(async () => {
      const t = await api<Topic>(
        "/topics",
        json("POST", {
          title: "ExampleSoft · 三步演示",
          description:
            "Track ExampleSoft vulnerabilities, affected versions and official patches. Keep conflicting reports for review.",
        }),
      );
      await api(
        `/topics/${t.topic_id}/sources`,
        json("PUT", { approved_source_ids: ["vendor", "research"] }),
      );
      setTopics(await api<Topic[]>("/topics"));
      selectTopic(t.topic_id);
      setProvider("mock");
      setDialog(null);
      setToast("演示已准备好。从「第一步：发现事件」开始。");
    });
  }
  const selectedEvidence = evidence.find(
    (e) => e.evidence.evidence_id === evidenceId,
  );
  const events = (dashboard?.events ?? []).filter((e) =>
    `${e.title} ${e.state.current_status}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const evidenceRows = (showHistorySources ? evidence : currentEvidence).filter(
    (e) =>
      `${e.evidence.title} ${e.evidence.content}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const latest = dashboard?.recent_runs[0];
  const unfinished = run?.evidence.filter((item) =>
    ["failed", "failed_retryable", "not_attempted"].includes(item.status),
  ) ?? [];
  const done = (id: string) =>
    dashboard?.recent_runs.some(
      (r) =>
        r.provider === "mock" &&
        r.fixture_batch_id === id &&
        r.status === "completed",
    );
  const nextDemo = demoSteps.find((s) => !done(s.id)) ?? demoSteps[2];

  const eventList = (
    <div className="event-table">
      {events.length ? (
        events.map((e, i) => (
          <button
            className="event-row"
            key={e.event_id}
            onClick={() => void openEvent(e.event_id)}
          >
            <span className="row-index">{String(i + 1).padStart(2, "0")}</span>
            <span className="event-row-main">
              <strong>{eventTitle(e.title)}</strong>
              <span>{text(e.state.key_facts.at(-1)?.fact ?? "")}</span>
            </span>
            <span
              className={`tag ${e.state.current_status === "patched" ? "green" : ""}`}
            >
              {status(e.state.current_status)}
            </span>
            <span className="event-row-meta">
              {e.state.key_facts.length} 条事实
              <br />
              {e.state.sources.length} 份依据
            </span>
            <Icon name="arrow" size={17} />
          </button>
        ))
      ) : (
        <Empty
          title="事件会在这里逐步形成"
          detail={
            fixture
              ? "运行第一批演示新闻，查看漏洞 A 如何被建档。"
              : "批准来源并运行检查。系统会按独立事项组织事件。"
          }
        />
      )}
    </div>
  );

  const runControls = (
    <aside className="run-panel">
      <div className="row-between">
        <span className="kicker">TRACKING SESSION</span>
        <span className={`status-dot ${running ? "working" : ""}`} />
      </div>
      <h3>{replay ? "按时间回放" : "检查新进展"}</h3>
      <p className="hint">
        {replay
          ? "每次只读取截止时间之前的新闻。"
          : "读取已批准来源，判断是否有值得记录的变化。"}
      </p>
      <label>
        分析方式
        <select
          value={provider}
          disabled={busy || running}
          onChange={(e) => setProvider(e.target.value as "mock" | "qwen")}
        >
          <option value="mock">规则演示 · ExampleSoft</option>
          <option value="qwen">真实模型分析</option>
        </select>
      </label>
      {provider === "qwen" && (
        <p className="model-note">
          {model?.configured ? model.model : "尚未配置模型"}{" "}
          <button
            className="text-button"
            onClick={() => showDialog("settings")}
          >
            {model?.configured ? "更改" : "去配置"}{" "}
            <Icon name="arrow" size={12} />
          </button>
        </p>
      )}
      {fixture && (
        <label>
          新闻批次
          <select
            value={batch}
            onChange={(e) => setBatch(e.target.value)}
            disabled={busy || running}
          >
            {demoSteps.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
            <option value="empty">空批次</option>
          </select>
        </label>
      )}
      {replay && (
        <>
          <label>
            回放语料
            <select
              value={corpus?.corpus_id ?? ""}
              disabled={busy || running}
              onChange={(e) => {
                setReplayCorpus(e.target.value);
                const next = replayCorpora.find(
                  (c) => c.corpus_id === e.target.value,
                );
                if (next) setAsOf(localInput(next.cutoffs[0]));
              }}
            >
              {replayCorpora.map((c) => (
                <option key={c.corpus_id} value={c.corpus_id}>
                  {c.name} · {c.records} 条
                </option>
              ))}
            </select>
          </label>
          <p className="hint">
            本次只读取选定的离线来源，避免混入模拟新闻、实时 RSS 或重复语料。
          </p>
          <label>
            回放截止 · 本地时间
            <input
              type="datetime-local"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              disabled={busy || running}
            />
          </label>
          <div className="cutoff-buttons">
            {corpus?.cutoffs.map((c, i) => (
              <button
                key={c}
                disabled={busy || running}
                className={asOf === localInput(c) ? "selected" : ""}
                onClick={() => setAsOf(localInput(c))}
              >
                <span>阶段 {i + 1}</span>
                <small>{date(c, false)}</small>
              </button>
            ))}
          </div>
          {provider === "mock" && (
            <p className="hint warning-text">
              这套体育语料需要真实模型；演示规则不会识别 NBA 事件。
            </p>
          )}
        </>
      )}
      <button
        className="primary full"
        disabled={busy || running || !dashboard?.approved_sources.length}
        onClick={() => void action(() => startRun())}
      >
        <Icon name="play" size={14} />
        {running
          ? "正在检查…"
          : provider === "qwen" && !model?.configured
            ? "配置模型后开始"
            : "开始检查"}
      </button>
      <button
        className="text-button full"
        disabled={busy || running}
        onClick={() => showDialog("manual")}
      >
        <Icon name="plus" size={15} /> 手动提交新闻
      </button>
      <div className="run-footnote">
        {replaySource ? 1 : (dashboard?.approved_sources.length ?? 0)}{" "}
        个本次来源 · {replay ? "离线回放" : "手动触发"}
        <br />
        {latest
          ? `最近检查 ${date(latest.completed_at ?? latest.started_at)}`
          : "尚未运行"}
      </div>
    </aside>
  );

  return (
    <div className="workspace">
      <aside className="sidebar">
        <a
          href="#"
          className="brand"
          onClick={(e) => {
            e.preventDefault();
            setTab("overview");
          }}
        >
          <span className="brand-mark">
            <i />
            <i />
            <i />
          </span>
          TraceRadar<span className="brand-period">.</span>
        </a>
        <div className="workspace-label">
          <span className="workspace-avatar">65</span>
          <span>
            Group 65<small>研究工作台</small>
          </span>
          <span className="sidebar-chevron">⌄</span>
        </div>
        <div className="sidebar-section-label">工作区</div>
        <nav className="side-nav">
          {tabs
            .filter((t) => ["overview", "review", "activity"].includes(t[0]))
            .map(([id, label, icon]) => (
              <button
                key={id}
                className={tab === id ? "selected" : ""}
                onClick={() => setTab(id)}
              >
                <Icon name={icon} />
                {label}
                {id === "review" && !!dashboard?.reviews.length && (
                  <span className="nav-count">{dashboard.reviews.length}</span>
                )}
              </button>
            ))}
        </nav>
        <div className="sidebar-section-label topic-label">
          追踪主题
          <button
            className="icon-button"
            disabled={busy}
            aria-label="新建追踪主题"
            onClick={() => showDialog("new")}
          >
            <Icon name="plus" size={15} />
          </button>
        </div>
        <div className="topic-list">
          {[...topics].reverse().map((t) => (
            <button
              disabled={busy}
              className={`topic-link ${selected === t.topic_id ? "selected" : ""}`}
              key={t.topic_id}
              onClick={() => selectTopic(t.topic_id)}
            >
              <span className="topic-dot" />
              <span>{t.title}</span>
            </button>
          ))}
          {!topics.length && <p className="sidebar-hint">创建第一个追踪主题</p>}
        </div>
        <button className="sidebar-replay" onClick={() => showDialog("replay")}>
          <Icon name="play" size={14} />
          <span>
            准备语料回放<small>LBJ / NBA</small>
          </span>
          <Icon name="arrow" size={14} />
        </button>
        <div className="sidebar-bottom">
          <button onClick={() => showDialog("help")}>
            <Icon name="help" />
            看懂这个演示
          </button>
          <button onClick={() => showDialog("settings")}>
            <Icon name="settings" />
            模型配置
            <span className={`tiny-dot ${model?.configured ? "online" : ""}`} />
          </button>
          <div className="sidebar-status">
            <span className={`status-dot ${health ? "online" : ""}`} />
            {health ? "本地服务在线" : "正在连接本地服务"}
            <span>v0.2</span>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            工作台 <span>/</span>{" "}
            {dashboard ? eventTitle(dashboard.topic.title) : "追踪主题"}
          </div>
          <div className="topbar-actions">
            <span className="local-label">LOCAL EDITION</span>
            <button
              className="icon-button mobile-new"
              aria-label="新建主题"
              disabled={busy}
              onClick={() => showDialog("new")}
            >
              <Icon name="plus" />
            </button>
            <button
              className="icon-button"
              aria-label="打开模型配置"
              onClick={() => showDialog("settings")}
            >
              <Icon name="settings" />
            </button>
          </div>
        </header>
        <main>
          <div className="page-heading">
            <div>
              <span className="kicker">A CONTINUOUS VIEW OF WHAT MATTERS</span>
              <h1>
                {dashboard
                  ? eventTitle(dashboard.topic.title)
                  : "让进展，有迹可循。"}
              </h1>
              <p>
                {dashboard?.topic.description ??
                  "把分散的信息组织成事件，保留每一次变化的依据。"}
              </p>
            </div>
            {dashboard && (
              <button
                className="heading-edit"
                disabled={busy || running}
                onClick={() => showDialog("goal")}
              >
                编辑追踪目标 <Icon name="arrow" size={14} />
              </button>
            )}
          </div>
          {error && !dialog && (
            <div role="alert" className="error">
              {error}
              <button
                className="icon-button"
                aria-label="关闭错误提示"
                onClick={() => setError("")}
              >
                <Icon name="close" size={14} />
              </button>
            </div>
          )}
          {toast && (
            <div role="status" className="toast">
              <Icon name="check" size={16} />
              {toast}
            </div>
          )}
          {loading && <p className="loading">正在读取工作区…</p>}
          {!selected && !loading && (
            <div className="welcome">
              <span className="kicker">START WITH A STORY</span>
              <h2>
                从一条信息，
                <br />
                看到事件的来龙去脉。
              </h2>
              <p>先用三批模拟新闻了解工作流程，或准备真实模型的新闻回放。</p>
              <div className="button-row">
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() => void createDemo()}
                >
                  创建三步演示 <Icon name="arrow" size={16} />
                </button>
                <button onClick={() => showDialog("new")}>新建主题</button>
              </div>
            </div>
          )}
          {dashboard && (
            <>
              <div className="topic-meta">
                <span
                  className={`tag ${dashboard.topic.status === "active" ? "green" : ""}`}
                >
                  <span className="tiny-dot online" />
                  {status(dashboard.topic.status)}
                </span>
                <span>{dashboard.approved_sources.length} 个来源</span>
                <span>
                  最近更新{" "}
                  {date(latest?.completed_at ?? dashboard.topic.updated_at)}
                </span>
                <span className="mode-label">
                  {latest ? providerLabel(latest.provider) : "尚未分析"}
                </span>
              </div>
              <div className="tabbar" role="tablist" aria-label="主题视图">
                {tabs.map(([id, label]) => (
                  <button
                    role="tab"
                    aria-selected={tab === id}
                    className={tab === id ? "selected" : ""}
                    key={id}
                    onClick={() => setTab(id)}
                  >
                    {label}
                    {id === "review" && !!dashboard.reviews.length && (
                      <span className="tab-count">
                        {dashboard.reviews.length}
                      </span>
                    )}
                  </button>
                ))}
                <label className="search-box">
                  <Icon name="search" size={15} />
                  <input
                    aria-label="筛选当前主题"
                    placeholder="筛选事件或证据"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
              </div>
              {tab === "overview" && (
                <div className="overview-layout">
                  <div className="overview-main">
                    <div className="summary-line">
                      <div>
                        <b>
                          {dashboard.events.length.toString().padStart(2, "0")}
                        </b>
                        <span>持续追踪的事件</span>
                      </div>
                      <div>
                        <b>
                          {currentEvidence.length.toString().padStart(2, "0")}
                        </b>
                        <span>当前来源的证据</span>
                      </div>
                      <div>
                        <b>
                          {dashboard.reviews.length.toString().padStart(2, "0")}
                        </b>
                        <span>等待人工判断</span>
                      </div>
                    </div>
                    {fixture && (
                      <div className="demo-strip">
                        <div className="demo-strip-icon">
                          <Icon name="help" size={19} />
                        </div>
                        <div>
                          <strong>你现在看到的是模拟演示</strong>
                          <p>
                            ExampleSoft
                            及三批新闻来自同学提交的仓库；“D验收”是开发验证主题。这些新闻可用规则演示，无需调用大模型。
                          </p>
                        </div>
                        <button
                          className="text-button"
                          onClick={() => showDialog("help")}
                        >
                          展开说明 <Icon name="arrow" size={14} />
                        </button>
                      </div>
                    )}
                    <section className="section-block">
                      <div className="section-title">
                        <h2>事件一览</h2>
                        <span className="meta">同一事件的后续报道持续归档</span>
                      </div>
                      {eventList}
                    </section>
                    {fixture && (
                      <section className="section-block">
                        <div className="section-title">
                          <h2>跟着演示走一遍</h2>
                          <span className="meta">
                            三批输入，观察状态如何演化
                          </span>
                        </div>
                        <ol className="demo-steps">
                          {demoSteps.map((s, i) => (
                            <li
                              key={s.id}
                              className={
                                done(s.id)
                                  ? "done"
                                  : nextDemo.id === s.id
                                    ? "next"
                                    : ""
                              }
                            >
                              <span className="step-number">
                                {done(s.id) ? (
                                  <Icon name="check" size={15} />
                                ) : (
                                  String(i + 1).padStart(2, "0")
                                )}
                              </span>
                              <div>
                                <h3>{s.name}</h3>
                                <p>{s.description}</p>
                              </div>
                              <button
                                disabled={busy || running}
                                onClick={() =>
                                  void action(() =>
                                    startRun({ batch: s.id, provider: "mock" }),
                                  )
                                }
                              >
                                {done(s.id) ? "再次运行" : "运行这一步"}{" "}
                                <Icon name="arrow" size={14} />
                              </button>
                            </li>
                          ))}
                        </ol>
                      </section>
                    )}
                    <section className="section-block">
                      <div className="section-title">
                        <h2>最近的提醒</h2>
                        <span className="meta">只在状态保存后生成</span>
                      </div>
                      {!notices.length ? (
                        <p className="blank-line">
                          有新的状态变化后，提醒会出现在这里。
                        </p>
                      ) : (
                        notices.slice(0, 5).map((n) => (
                          <button
                            className="notification-row"
                            key={n.notification_id}
                            onClick={() => void openEvent(n.event_id)}
                          >
                            <span className={`notice-indicator ${n.action}`} />
                            <span>
                              <strong>
                                {eventTitle(
                                  dashboard.events.find(
                                    (e) => e.event_id === n.event_id,
                                  )?.title ?? "事件更新",
                                )}
                              </strong>
                              <small>
                                {n.reason_codes.includes("human_review")
                                  ? "人工审核后更新"
                                  : n.reason_codes.includes("major_milestone")
                                    ? "事件状态出现重大进展"
                                    : n.reason_codes.includes("event_created")
                                      ? "发现一个独立事件"
                                      : "补充了新的事件信息"}
                              </small>
                            </span>
                            <span className="meta">
                              {actionLabel(n.action)}
                              <small>{date(n.created_at)}</small>
                            </span>
                          </button>
                        ))
                      )}
                    </section>
                  </div>
                  {runControls}
                </div>
              )}
              {tab === "events" && (
                <section className="section-block">
                  <div className="section-title">
                    <h2>全部事件</h2>
                    <span className="meta">选择一行查看事实、时间线与依据</span>
                  </div>
                  {eventList}
                </section>
              )}
              {tab === "evidence" && (
                <section className="section-block">
                  <div className="section-title">
                    <h2>证据库</h2>
                    <span className="meta">包括被排除、重复与待审的信息</span>
                  </div>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={showHistorySources}
                      onChange={(e) => setShowHistorySources(e.target.checked)}
                    />
                    显示全部来源的历史记录
                  </label>
                  {!evidenceRows.length ? (
                    <Empty
                      title="还没有读入的新闻"
                      detail="运行一次检查，或手动提交一条新闻。"
                    />
                  ) : (
                    <div className="evidence-table">
                      {evidenceRows.map((v, i) => {
                        const duplicate =
                          v.evidence.canonical_evidence_id !==
                            v.evidence.evidence_id &&
                          !v.assessment &&
                          v.status === "completed";
                        const label =
                          v.status === "review"
                            ? "待审核"
                            : ["failed", "failed_retryable", "pending"].includes(v.status)
                              ? status(v.status)
                              : duplicate
                                ? "重复报道"
                                : v.assessment?.is_relevant === false
                                  ? "已排除"
                                  : "已处理";
                        return (
                          <button
                            className="evidence-row"
                            key={v.evidence.evidence_id}
                            onClick={() =>
                              setEvidenceId(v.evidence.evidence_id)
                            }
                          >
                            <span className="row-index">
                              {String(i + 1).padStart(2, "0")}
                            </span>
                            <span>
                              <strong>{headline(v.evidence.title)}</strong>
                              <small>
                                {v.evidence.source_id} ·{" "}
                                {date(v.evidence.published_at)}
                              </small>
                            </span>
                            <span
                              className={`tag ${v.status === "review" ? "amber" : ""}`}
                            >
                              {label}
                            </span>
                            <Icon name="arrow" size={15} />
                          </button>
                        );
                      })}
                    </div>
                  )}
                </section>
              )}
              {tab === "review" && (
                <ReviewPanel
                  key={selected}
                  topicId={selected}
                  reviews={dashboard.reviews}
                  resolutions={dashboard.review_resolutions}
                  events={dashboard.events}
                  disabled={busy || running}
                  action={action}
                  onSaved={async () => {
                    await refresh();
                    setToast("审核已记录，事件与历史已刷新。");
                  }}
                />
              )}
              {tab === "sources" && (
                <div className="sources-layout">
                  <section className="section-block">
                    <div className="section-title">
                      <h2>信息来源</h2>
                      <button
                        disabled={busy || running}
                        onClick={() => showDialog("source")}
                      >
                        <Icon name="plus" size={15} /> 添加来源
                      </button>
                    </div>
                    <p className="section-description">
                      只有你勾选并保存的来源会进入这个主题的检查流程。
                    </p>
                    <div className="source-list">
                      {sources
                        .filter(
                          (s) =>
                            !s.source_id.startsWith("gnews_") ||
                            s.source_id === `gnews_${selected.slice(0, 8)}` ||
                            approved.includes(s.source_id),
                        )
                        .map((s) => (
                          <label
                            className={`source-row ${approved.includes(s.source_id) ? "checked" : ""}`}
                            key={s.source_id}
                          >
                            <input
                              type="checkbox"
                              disabled={busy || running}
                              checked={approved.includes(s.source_id)}
                              onChange={(e) =>
                                setApproved(
                                  e.target.checked
                                    ? [...approved, s.source_id]
                                    : approved.filter(
                                        (id) => id !== s.source_id,
                                      ),
                                )
                              }
                            />
                            <span className="source-icon">
                              <Icon
                                name={
                                  s.type === "rss"
                                    ? "sources"
                                    : s.type === "manual"
                                      ? "plus"
                                      : "evidence"
                                }
                                size={19}
                              />
                            </span>
                            <span>
                              <strong>{s.name}</strong>
                              <small>{s.endpoint}</small>
                              <small className="source-id">{s.source_id}</small>
                            </span>
                            <span className="tag">
                              {
                                {
                                  fixture: "模拟来源",
                                  replay: "离线回放",
                                  rss: "RSS",
                                  manual: "手动录入",
                                }[s.type]
                              }
                            </span>
                          </label>
                        ))}
                    </div>
                    <div className="section-actions">
                      <button
                        className="primary"
                        disabled={busy || running}
                        onClick={() =>
                          void action(async () => {
                            await api(
                              `/topics/${selected}/sources`,
                              json("PUT", { approved_source_ids: approved }),
                            );
                            await refresh();
                            setTopics(await api<Topic[]>("/topics"));
                            setToast("已保存这个主题的信息来源。");
                          })
                        }
                      >
                        保存批准来源 <Icon name="check" size={15} />
                      </button>
                    </div>
                  </section>
                  <aside className="context-panel">
                    <span className="kicker">YOUR PREFERENCES</span>
                    <h3>提醒偏好</h3>
                    <label>
                      何时提醒
                      <select
                        value={dashboard.topic.notification_preferences}
                        disabled={busy || running}
                        onChange={(e) =>
                          void action(async () => {
                            await api(
                              `/topics/${selected}`,
                              json("PATCH", {
                                notification_preferences: e.target.value,
                              }),
                            );
                            await refresh();
                          })
                        }
                      >
                        <option value="standard">重大进展即时提醒</option>
                        <option value="digest_only">全部进入更新汇总</option>
                      </select>
                    </label>
                    <p className="hint">
                      普通更新进入汇总，低优先级变化仅保留记录。目前提醒展示在工作台内。
                    </p>
                    <div className="context-divider" />
                    <h3>想测试体育语料？</h3>
                    <p className="hint">
                      单独创建回放主题，避免混入模拟新闻和实时订阅。
                    </p>
                    <button
                      className="text-button"
                      onClick={() => showDialog("replay")}
                    >
                      准备回放工作区 <Icon name="arrow" size={14} />
                    </button>
                  </aside>
                </div>
              )}
              {tab === "activity" && (
                <div className="activity-layout">
                  <section className="run-list">
                    <span className="kicker">SESSIONS</span>
                    <h2>检查历史</h2>
                    {dashboard.recent_runs.map((r) => (
                      <button
                        className={`session-row ${run?.run.run_id === r.run_id ? "selected" : ""}`}
                        key={r.run_id}
                        onClick={() =>
                          void action(async () =>
                            setRun(await api<RunDetail>(`/runs/${r.run_id}`)),
                          )
                        }
                      >
                        <span
                          className={`status-dot ${r.status === "completed" ? "online" : active(r.status) ? "working" : ""}`}
                        />
                        <span>
                          <strong>{providerLabel(r.provider)}</strong>
                          <small>
                            {date(r.started_at)} · {status(r.status)}
                          </small>
                        </span>
                        <Icon name="arrow" size={14} />
                      </button>
                    ))}
                  </section>
                  <section className="run-detail">
                    {run ? (
                      <>
                        <div className="section-title">
                          <h2>
                            {providerLabel(run.run.provider)}{" "}
                            <span className="tag">
                              {status(run.run.status)}
                            </span>
                          </h2>
                          <span className="meta">
                            {date(run.run.started_at)}
                          </span>
                        </div>
                        <div className="run-summary">
                          读取 <b>{run.run.counts.fetched ?? 0}</b> · 状态更新{" "}
                          <b>{run.run.counts.committed ?? 0}</b> · 跳过{" "}
                          <b>{run.run.counts.skipped ?? 0}</b> · 待审{" "}
                          <b>{run.run.counts.review ?? 0}</b>
                        </div>
                        {run.run.error != null && (
                          <pre className="error">{pretty(run.run.error)}</pre>
                        )}
                        {unfinished.length > 0 && !active(run.run.status) && (
                          <>
                            <div className="section-title">
                              <h3>失败或未处理的证据</h3>
                              <button
                                disabled={running || busy}
                                onClick={() => void action(() => resumeRun(run.run.run_id))}
                              >
                                续跑未完成证据
                              </button>
                            </div>
                            <ul>
                              {unfinished.map((item) => (
                                <li key={`${item.source_id}:${item.external_id}`}>
                                  <strong>{item.title}</strong>{" "}
                                  <span className="meta">
                                    {item.external_id} ·{" "}
                                    {status(item.status)}
                                    {" · "}{item.attempts} 次尝试
                                    {item.error && ` · ${item.error.message}`}
                                  </span>
                                </li>
                              ))}
                            </ul>
                          </>
                        )}
                        <ol className="trace-list">
                          {run.steps.map((s) => (
                            <li key={s.step_id}>
                              <span className={`trace-dot ${s.status}`} />
                              <details>
                                <summary>
                                  <span>{stepLabel(s.step_name)}</span>
                                  <span className="meta">
                                    {status(s.status)} · {s.attempt} 次
                                  </span>
                                </summary>
                                <div className="trace-human">
                                  {s.evidence_id && (
                                    <button
                                      className="text-button"
                                      onClick={() =>
                                        setEvidenceId(s.evidence_id!)
                                      }
                                    >
                                      查看处理的新闻{" "}
                                      <Icon name="arrow" size={13} />
                                    </button>
                                  )}
                                  <small>
                                    {date(s.started_at)} →{" "}
                                    {date(s.completed_at)}
                                  </small>
                                  <pre>{pretty(s.error ?? s.decision)}</pre>
                                </div>
                              </details>
                            </li>
                          ))}
                        </ol>
                        <details className="quiet-details">
                          <summary>完整运行记录</summary>
                          <pre>{pretty(run.run)}</pre>
                        </details>
                      </>
                    ) : (
                      <Empty
                        title="还没有运行记录"
                        detail="每次检查都会留下有顺序的步骤、处理结果和错误。"
                      />
                    )}
                  </section>
                </div>
              )}
            </>
          )}
          <footer>
            <span>TRACERADAR / GROUP 65</span>
            <span>把结论留给证据，把变化留在历史。</span>
          </footer>
        </main>
      </div>
      {dialog && (
        <Modal
          title={
            {
              new: "新建追踪主题",
              goal: "编辑追踪目标",
              source: "添加信息来源",
              manual: "提交一条新闻",
              settings: "配置真实模型",
              replay: "准备新闻回放",
              help: "看懂 ExampleSoft 演示",
            }[dialog]
          }
          onClose={() => setDialog(null)}
          wide={dialog === "help"}
        >
          {error && (
            <div role="alert" className="error">
              {error}
            </div>
          )}
          {dialog === "new" && (
            <form
              className="form-stack"
              onSubmit={(e) => {
                e.preventDefault();
                void action(async () => {
                  const t = await api<Topic>(
                    "/topics",
                    json("POST", {
                      title: newTitle,
                      description: newDescription,
                    }),
                  );
                  setTopics(await api<Topic[]>("/topics"));
                  selectTopic(t.topic_id);
                  setDialog(null);
                  setTab("sources");
                  setNewTitle("");
                  setNewDescription("");
                });
              }}
            >
              <label>
                主题名称
                <input
                  autoFocus
                  required
                  maxLength={200}
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="例如：某公司的收购进展"
                />
              </label>
              <label>
                你想持续了解什么？
                <textarea
                  required
                  maxLength={4000}
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder="说明关注的事项、重要变化和需要区分的情况。"
                />
              </label>
              <button className="primary" disabled={busy}>
                创建主题 <Icon name="arrow" size={16} />
              </button>
            </form>
          )}
          {dialog === "goal" && dashboard && (
            <GoalEditor
              topic={dashboard.topic}
              disabled={busy || running}
              action={action}
              onSaved={async () => {
                await refresh();
                setTopics(await api<Topic[]>("/topics"));
                setDialog(null);
                setToast("追踪目标已更新。");
              }}
            />
          )}
          {dialog === "source" && (
            <RegisterSource
              disabled={busy || running}
              action={action}
              onSaved={async () => {
                await refreshSources();
                setDialog(null);
                setTab("sources");
                setToast("来源已添加，请勾选并保存批准。");
              }}
              knownIds={sources.map((s) => s.source_id)}
            />
          )}
          {dialog === "manual" && dashboard && (
            <ManualEvidenceForm
              sources={dashboard.approved_sources}
              disabled={busy || running}
              submit={(item) =>
                action(() =>
                  startRun({
                    manualItems: [item],
                    batch: replay ? "all" : "empty",
                  }),
                )
              }
            />
          )}
          {dialog === "settings" && (
            <ModelSettingsPanel
              value={model}
              disabled={busy || running}
              action={action}
              onSaved={setModel}
            />
          )}
          {dialog === "replay" && (
            <ReplaySetup
              corpora={corpora}
              disabled={busy}
              action={action}
              onPrepared={async (t, c) => {
                setTopics(await api<Topic[]>("/topics"));
                selectTopic(t.topic_id);
                setProvider("qwen");
                setAsOf(localInput(c.cutoffs[0]));
                setDialog(null);
                setToast("回放工作区已准备好。配置模型后，从阶段 1 开始。");
              }}
            />
          )}
          {dialog === "help" && (
            <div className="help-content">
              <span className="tag amber">全部为模拟数据</span>
              <h3>ExampleSoft 是虚构公司，来自同学原有的代码。</h3>
              <p>
                仓库原本就有 ExampleSoft
                的三批新闻、规则判断器和演示页面。我增加了“D验收”测试主题，用来检查新增功能。“D验收”本身不是产品功能。
              </p>
              <div className="help-flow">
                {demoSteps.map((s, i) => (
                  <div key={s.id}>
                    <b>0{i + 1}</b>
                    <h4>{s.name}</h4>
                    <p>{s.description}</p>
                  </div>
                ))}
              </div>
              <h3>这些栏目各自回答什么问题？</h3>
              <dl className="help-definitions">
                <div>
                  <dt>事件</dt>
                  <dd>同一件事现在发展到哪里？例如漏洞 A，从披露到修复。</dd>
                </div>
                <div>
                  <dt>证据</dt>
                  <dd>这个判断来自哪篇新闻？排除与重复的信息也能查看。</dd>
                </div>
                <div>
                  <dt>时间线与历史</dt>
                  <dd>什么时候发生变化？系统之前和现在分别怎样判断？</dd>
                </div>
                <div>
                  <dt>待审核</dt>
                  <dd>哪里有矛盾？人可以保留原状态，或核对后采纳并修正。</dd>
                </div>
                <div>
                  <dt>运行记录</dt>
                  <dd>这次读取了什么、跳过了什么、在哪一步失败？</dd>
                </div>
              </dl>
              <p className="hint">
                规则演示验证工作流程；真实模型才能分析其他主题。演示运行成功，不代表模型效果已经通过评测。
              </p>
              <button
                className="primary"
                disabled={busy}
                onClick={() => void createDemo()}
              >
                新建一个干净的三步演示 <Icon name="arrow" size={15} />
              </button>
            </div>
          )}
        </Modal>
      )}
      {event && (
        <Modal title="事件档案" onClose={() => setEvent(null)} wide sheet>
          <EventView value={event} onEvidence={setEvidenceId} />
        </Modal>
      )}
      {selectedEvidence && (
        <Modal title="新闻与判断依据" onClose={() => setEvidenceId("")}>
          <EvidenceView value={selectedEvidence} />
        </Modal>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
