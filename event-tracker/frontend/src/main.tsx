import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
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
import { OverviewSection, OverviewSummary } from "./overview";
import "@fontsource-variable/host-grotesk/wght.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
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
import "./workspace-v11.css";

const tabs = [
  ["overview", "概览", "grid"],
  ["events", "事件", "events"],
  ["evidence", "证据库", "evidence"],
  ["review", "待审核", "review"],
  ["sources", "信息源", "sources"],
  ["activity", "运行记录", "activity"],
] as const;
type Tab = (typeof tabs)[number][0];
const fixtureOnly = (sources: Source[]) =>
  sources.some((source) => source.type === "fixture") &&
  sources.every((source) => ["fixture", "manual"].includes(source.type));
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
  const [llmSummaryOn, setLlmSummaryOn] = useState(false);
  const [llmSummaryNote, setLlmSummaryNote] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState(""),
    [newDescription, setNewDescription] = useState("");
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const viewedRun = useRef("");
  const actionPending = useRef(false);
  const tabStrip = useRef<HTMLDivElement>(null);
  const dashboardReady = Boolean(dashboard);
  const running = active(dashboard?.recent_runs[0]?.status);
  const replay = dashboard?.approved_sources.some((s) => s.type === "replay");
  const fixture = fixtureOnly(dashboard?.approved_sources ?? []);
  const replayCorpora = corpora.filter((c) =>
    dashboard?.approved_sources.some(
      (s) => s.type === "replay" && s.config.corpus_path === c.corpus_path,
    ),
  );
  const corpus =
    replayCorpora.find((c) => c.corpus_id === replayCorpus) ?? replayCorpora[0];
  const replaySources =
    dashboard?.approved_sources.filter(
      (s) => s.type === "replay" && s.config.corpus_path === corpus?.corpus_path,
    ) ?? [];
  const replaySource = replaySources[0];
  const candidateBranchCount = sources.filter(
    (s) =>
      s.source_id.startsWith(`kw_${selected.slice(0, 8)}_`) &&
      !approved.includes(s.source_id),
  ).length;
  const retirementAlertCount = sources.filter(
    (s) => approved.includes(s.source_id) && s.retirement_recommended,
  ).length;
  const savedApprovedIds = (dashboard?.approved_sources ?? [])
    .map((s) => s.source_id)
    .sort()
    .join(",");
  const hasUnsavedSources =
    [...approved].sort().join(",") !== savedApprovedIds;
  const currentEvidence = evidence.filter((v) =>
    dashboard?.approved_sources.some(
      (s) => s.source_id === v.evidence.source_id,
    ),
  );
  const manualAvailable = dashboard?.approved_sources.some(
    (s) => s.type === "manual",
  );

  useLayoutEffect(() => {
    const strip = tabStrip.current;
    const target = strip?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!strip || !target) return;
    const moveIndicator = () => {
      strip.style.setProperty("--tab-x", `${target.offsetLeft + 8}px`);
      strip.style.setProperty("--tab-width", `${target.offsetWidth - 16}px`);
    };
    const resize = new ResizeObserver(moveIndicator);
    Array.from(strip.children).forEach((child) => resize.observe(child));
    moveIndicator();
    return () => resize.disconnect();
  }, [tab, dashboardReady]);

  function openTab(id: Tab, focus = false) {
    setTab(id);
    if (focus) {
      requestAnimationFrame(() => {
        const panel = document.getElementById("workspace-panel");
        panel?.focus({ preventScroll: true });
        document.querySelector(".tabbar")?.scrollIntoView({ block: "start" });
      });
    }
  }

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
    viewedRun.current = "";
    setEvidence([]);
    setSources([]);
    setNotices([]);
    setLlmSummaryOn(false);
    setLlmSummaryNote(null);
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
        setProvider(fixtureOnly(d.approved_sources) ? "mock" : "qwen");
        const availableCorpora = corpora.filter((c) =>
          d.approved_sources.some(
            (s) =>
              s.type === "replay" && s.config.corpus_path === c.corpus_path,
          ),
        );
        const preferred =
          /麥當勞|麦当劳|mcdonald/i.test(d.topic.title) &&
          availableCorpora.some((c) => c.corpus_id === "mcd_hk_real")
            ? "mcd_hk_real"
            : /nba/i.test(d.topic.title) &&
                !/lebron|lbj|詹姆斯/i.test(d.topic.title)
              ? "nba"
              : availableCorpora.some((c) => c.corpus_id === "lbj_daily")
                ? "lbj_daily"
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
          if (!viewedRun.current) setRun(detail);
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

  async function refresh(sourceSettingsChanged = false) {
    const id = selected;
    const [d, n, e, rec] = await Promise.all([
      api<Dashboard>(`/topics/${id}/dashboard`),
      api<Notification[]>(`/topics/${id}/notifications`),
      api<EvidenceDecision[]>(`/topics/${id}/evidence`),
      api<{ sources: Source[] }>(
        `/topics/${id}/source-recommendations`,
        json("POST", {}),
      ).catch(() => null),
    ]);
    if (selectedRef.current !== id) return;
    setDashboard(d);
    setNotices(n);
    setEvidence(e);
    if (rec) setSources(rec.sources);
    if (sourceSettingsChanged) {
      setApproved(d.approved_sources.map((s) => s.source_id));
      setProvider(fixtureOnly(d.approved_sources) ? "mock" : "qwen");
      const activeReplay = d.approved_sources.filter(
        (s) => s.type === "replay",
      );
      const nextCorpus = corpora.find((c) =>
        activeReplay.some((s) => s.config.corpus_path === c.corpus_path),
      );
      setReplayCorpus(nextCorpus?.corpus_id ?? "");
      if (!asOf && nextCorpus?.cutoffs[0]) {
        setAsOf(localInput(nextCorpus.cutoffs[0]));
      }
      setBatch(nextCorpus ? "all" : "1");
    }
  }
  async function refreshSources(wantLlmSummary = llmSummaryOn) {
    const s = await api<{ sources: Source[]; llm_summary_note?: string | null }>(
      `/topics/${selected}/source-recommendations`,
      json("POST", { llm_summary: wantLlmSummary }),
    );
    setSources(s.sources);
    setLlmSummaryNote(s.llm_summary_note ?? null);
  }
  async function saveApprovedSources() {
    await api(
      `/topics/${selected}/sources`,
      json("PUT", { approved_source_ids: approved }),
    );
    if (dashboard?.topic) {
      const branchLabels = sources
        .filter(
          (s) =>
            approved.includes(s.source_id) &&
            typeof s.config?.branch_label === "string",
        )
        .map((s) => String(s.config.branch_label));
      const baseDesc = dashboard.topic.description
        .replace(/\s*【关联支线关注：[^】]*】/g, "")
        .trim();
      const nextDesc = branchLabels.length
        ? `${baseDesc} 【关联支线关注：同时追踪 ${branchLabels.join("、")} 的相关最新进展与细节】`
        : baseDesc;
      if (nextDesc !== dashboard.topic.description) {
        await api(
          `/topics/${selected}`,
          json("PATCH", { description: nextDesc }),
        );
      }
    }
    await refresh(true);
    setTopics(await api<Topic[]>("/topics"));
    setToast("已保存这个主题的追踪词表与信息来源。");
  }
  async function action(task: () => Promise<void>) {
    if (actionPending.current) return;
    actionPending.current = true;
    setBusy(true);
    setError("");
    setToast("");
    try {
      await task();
    } catch (e) {
      setError(String(e));
    } finally {
      actionPending.current = false;
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
    let retries = 0;
    async function poll() {
      try {
        const detail = await api<RunDetail>(`/runs/${watchId}`);
        if (disposed || detail.run.topic_id !== selected) return;
        if (!viewedRun.current || viewedRun.current === detail.run.run_id)
          setRun(detail);
        await refresh();
        retries = 0;
        if (!disposed) {
          if (active(detail.run.status)) timer = setTimeout(poll, 900);
          else {
            setWatchId("");
            setToast(
              detail.run.status === "completed"
                ? "检查已完成，工作区已更新。"
                : "检查已结束。请在运行记录中查看处理结果。",
            );
          }
        }
      } catch (e) {
        if (disposed) return;
        if (retries++ < 3) timer = setTimeout(poll, 1200);
        else setError(String(e));
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
      manualItems?: Record<string, string>[];
    } = {},
  ) {
    const manual = Boolean(options.manualItems?.length);
    const engine = options.provider ?? provider;
    const cutoff = replay && !manual ? asOf : "";
    const sourceIds = manual
      ? options.manualItems!.map((item) => item.source_id)
      : replaySources.length
        ? replaySources.map((s) => s.source_id)
        : undefined;
    if (engine === "qwen" && !model?.configured) {
      setDialog("settings");
      throw Error("请先填写模型服务配置。");
    }
    if (replay && !manual && !replaySource)
      throw Error("请选择可用的回放语料。");
    if (replay && !manual && !cutoff) throw Error("请选择回放截止时间。");
    const result = await api<{ run_id: string }>(
      `/topics/${selected}/runs`,
      json("POST", {
        fixture_batch_id: manual
          ? "empty"
          : replay
            ? "all"
            : (options.batch ?? batch),
        ...(sourceIds ? { source_ids: [...new Set(sourceIds)] } : {}),
        provider: engine,
        ...(cutoff ? { as_of: new Date(cutoff).toISOString() } : {}),
        ...(options.manualItems ? { manual_items: options.manualItems } : {}),
      }),
    );
    setEvent(null);
    viewedRun.current = result.run_id;
    setWatchId(result.run_id);
    setDialog(null);
    setToast(
      manual
        ? "新闻已提交，正在处理。"
        : "检查已启动，可在运行记录中查看进度。",
    );
    await refresh();
  }
  async function openEvent(id: string) {
    await action(async () => setEvent(await api<EventDetail>(`/events/${id}`)));
  }
  async function resumeRun(id: string) {
    const result = await api<{ run_id: string }>(
      `/runs/${id}/resume`,
      json("POST", {}),
    );
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
  const search = query.trim().toLowerCase();
  const events = (dashboard?.events ?? []).filter((e) =>
    `${e.title} ${eventTitle(e.title)} ${e.state.current_status} ${status(e.state.current_status)} ${e.state.key_facts.map((fact) => text(fact.fact)).join(" ")}`
      .toLowerCase()
      .includes(search),
  );
  const evidenceRows = (showHistorySources ? evidence : currentEvidence).filter(
    (e) =>
      `${e.evidence.title} ${headline(e.evidence.title)} ${e.evidence.source_id} ${e.evidence.content}`
        .toLowerCase()
        .includes(search),
  );
  const latest = dashboard?.recent_runs[0];
  const unfinished =
    run?.evidence?.filter((item) =>
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
            disabled={busy}
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
          title={search ? "没有符合筛选条件的事件" : "事件会在这里逐步形成"}
          detail={
            search
              ? "试试事件名称、状态或事实中的其他关键词。"
              : fixture
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
          <option
            value="mock"
            disabled={!fixtureOnly(dashboard?.approved_sources ?? [])}
          >
            规则演示 · ExampleSoft
          </option>
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
        </>
      )}
      <button
        className="primary full"
        disabled={busy || running || !dashboard?.approved_sources.length}
        onClick={() =>
          provider === "qwen" && !model?.configured
            ? showDialog("settings")
            : void action(() => startRun())
        }
      >
        <Icon name="play" size={14} />
        {running
          ? "正在检查…"
          : provider === "qwen" && !model?.configured
            ? "打开模型配置"
            : "开始检查"}
      </button>
      <button
        className="text-button full"
        disabled={busy || running}
        onClick={() => {
          if (!manualAvailable) openTab("sources", true);
          else if (provider === "qwen" && !model?.configured)
            showDialog("settings");
          else showDialog("manual");
        }}
      >
        <Icon name="plus" size={15} />{" "}
        {manualAvailable ? "手动提交新闻" : "管理手动来源"}
      </button>
      <div className="run-footnote">
        {replaySources.length ? replaySources.length : (dashboard?.approved_sources.length ?? 0)}{" "}
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
          <span className="brand-mark" aria-hidden="true">
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
        </div>
        <div className="sidebar-section-label">工作区</div>
        <nav className="side-nav">
          {tabs
            .filter((t) => ["overview", "review", "activity"].includes(t[0]))
            .map(([id, label, icon]) => (
              <button
                key={id}
                className={tab === id ? "selected" : ""}
                onClick={() => openTab(id, true)}
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
          <button aria-label="看懂这个演示" onClick={() => showDialog("help")}>
            <Icon name="help" />
            看懂这个演示
          </button>
          <button aria-label="模型配置" onClick={() => showDialog("settings")}>
            <Icon name="settings" />
            模型配置
            <span className={`tiny-dot ${model?.configured ? "online" : ""}`} />
          </button>
          <div className="sidebar-status">
            <span className={`status-dot ${health ? "online" : ""}`} />
            {health
              ? "本地服务在线"
              : loading
                ? "正在连接本地服务"
                : "本地服务不可用"}
            <span>v1.1</span>
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
            <button
              className="icon-button mobile-replay"
              aria-label="准备语料回放"
              onClick={() => showDialog("replay")}
            >
              <Icon name="play" />
            </button>
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
              <span className="kicker">TRACERADAR / WORKSPACE</span>
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
              <div
                className="tabbar"
                ref={tabStrip}
                role="tablist"
                aria-label="主题视图"
                onKeyDown={(e) => {
                  const index = tabs.findIndex(([id]) => id === tab);
                  const next = {
                    ArrowLeft: (index + tabs.length - 1) % tabs.length,
                    ArrowRight: (index + 1) % tabs.length,
                    Home: 0,
                    End: tabs.length - 1,
                  }[e.key];
                  if (next === undefined) return;
                  e.preventDefault();
                  const [id] = tabs[next];
                  openTab(id);
                  document.getElementById(`tab-${id}`)?.focus();
                }}
              >
                {tabs.map(([id, label, icon]) => (
                  <button
                    role="tab"
                    id={`tab-${id}`}
                    aria-controls="workspace-panel"
                    aria-selected={tab === id}
                    tabIndex={tab === id ? 0 : -1}
                    className={tab === id ? "selected" : ""}
                    key={id}
                    onClick={() => openTab(id)}
                  >
                    <Icon name={icon} size={15} />
                    {label}
                    {id === "review" && !!dashboard.reviews.length && (
                      <span className="tab-count">
                        {dashboard.reviews.length}
                      </span>
                    )}
                    {id === "sources" && (candidateBranchCount > 0 || retirementAlertCount > 0) && (
                      <span className="tab-count">
                        {retirementAlertCount > 0
                          ? `⚠${retirementAlertCount}`
                          : `+${candidateBranchCount}`}
                      </span>
                    )}
                  </button>
                ))}
                <span className="tab-indicator" aria-hidden="true" />
              </div>
              {(["overview", "events", "evidence"] as Tab[]).includes(tab) && (
                <label className="search-box">
                  <Icon name="search" size={17} />
                  <input
                    type="search"
                    aria-label={tab === "evidence" ? "筛选证据" : "筛选事件"}
                    placeholder={
                      tab === "evidence"
                        ? "筛选标题、来源或内容"
                        : "筛选事件、状态或事实"
                    }
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
              )}
              <div
                key={tab}
                className="workspace-panel"
                data-view={tab}
                id="workspace-panel"
                role="tabpanel"
                aria-labelledby={`tab-${tab}`}
                tabIndex={0}
              >
                {tab === "overview" && (
                  <div className="overview-layout">
                    <div className="overview-main">
                      <OverviewSummary
                        metrics={[
                          {
                            value: dashboard.events.length,
                            label: "追踪事件",
                            open: () => openTab("events", true),
                          },
                          {
                            value: currentEvidence.length,
                            label: "当前来源证据",
                            open: () => openTab("evidence", true),
                          },
                          {
                            value: dashboard.reviews.length,
                            label: "待审核",
                            open: () => openTab("review", true),
                          },
                        ]}
                      />
                      <div className="overview-sections">
                        <OverviewSection
                          title="事件一览"
                          label="查看全部事件"
                          onOpen={() => openTab("events", true)}
                        >
                          {eventList}
                        </OverviewSection>
                        <OverviewSection
                          title="近期证据"
                          label="进入证据库"
                          onOpen={() => openTab("evidence", true)}
                        >
                          {currentEvidence.length ? (
                            [...currentEvidence]
                              .sort(
                                (a, b) =>
                                  Date.parse(b.evidence.published_at) -
                                  Date.parse(a.evidence.published_at),
                              )
                              .slice(0, 3)
                              .map(
                                ({
                                  evidence: item,
                                  status: evidenceStatus,
                                }) => (
                                  <button
                                    className="recent-evidence"
                                    key={item.evidence_id}
                                    onClick={() =>
                                      setEvidenceId(item.evidence_id)
                                    }
                                  >
                                    <Icon name="evidence" size={17} />
                                    <span>
                                      <strong>{headline(item.title)}</strong>
                                      <small>
                                        {item.source_id} ·{" "}
                                        {date(item.published_at, false)}
                                      </small>
                                    </span>
                                    {evidenceStatus === "review" && (
                                      <span className="tag amber">待审核</span>
                                    )}
                                  </button>
                                ),
                              )
                          ) : (
                            <p className="blank-line">
                              开始检查后，最新读入的证据会出现在这里。
                            </p>
                          )}
                        </OverviewSection>
                        <OverviewSection
                          title="变化与提醒"
                          label="查看审核队列"
                          onOpen={() => openTab("review", true)}
                        >
                          {!!dashboard.reviews.length && (
                            <p className="review-callout">
                              {dashboard.reviews.length} 条证据等待人工判断
                            </p>
                          )}
                          {!notices.length ? (
                            <p className="blank-line">
                              有新的状态变化后，提醒会出现在这里。
                            </p>
                          ) : (
                            notices.slice(0, 5).map((n) => (
                              <button
                                className="notification-row"
                                key={n.notification_id}
                                disabled={busy}
                                onClick={() => void openEvent(n.event_id)}
                              >
                                <span
                                  className={`notice-indicator ${n.action}`}
                                />
                                <span>
                                  <strong>
                                    {eventTitle(
                                      dashboard.events.find(
                                        (e) => e.event_id === n.event_id,
                                      )?.title ?? "事件更新",
                                    )}
                                  </strong>
                                  <small>{actionLabel(n.action)}</small>
                                </span>
                                <span className="meta">
                                  {date(n.created_at)}
                                </span>
                              </button>
                            ))
                          )}
                        </OverviewSection>
                      </div>
                      {fixture && (
                        <section className="section-block">
                          <div className="section-title">
                            <h2>跟着演示走一遍</h2>
                            <button
                              className="text-button"
                              onClick={() => showDialog("help")}
                            >
                              模拟演示说明
                            </button>
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
                                      startRun({
                                        batch: s.id,
                                        provider: "mock",
                                      }),
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
                    </div>
                    {runControls}
                  </div>
                )}
                {tab === "events" && (
                  <section className="section-block">
                    <div className="section-title">
                      <h2>全部事件</h2>
                      <span className="meta">
                        选择一行查看事实、时间线与依据
                      </span>
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
                        onChange={(e) =>
                          setShowHistorySources(e.target.checked)
                        }
                      />
                      显示全部来源的历史记录
                    </label>
                    {!evidenceRows.length ? (
                      <Empty
                        title={
                          search ? "没有符合筛选条件的证据" : "还没有读入的新闻"
                        }
                        detail={
                          search
                            ? "试试其他关键词，或查阅全部来源的历史记录。"
                            : "运行一次检查，或手动提交一条新闻。"
                        }
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
                {tab === "sources" && (() => {
                  const visibleSources = sources.filter(
                    (s) =>
                      !s.source_id.startsWith("gnews_") ||
                      s.source_id === `gnews_${selected.slice(0, 8)}` ||
                      approved.includes(s.source_id),
                  );
                  const isWatchlist = (s: Source) =>
                    s.role === "anchor" ||
                    s.role === "branch" ||
                    s.source_id.startsWith("kw_") ||
                    s.source_id.startsWith("lbj_anchor_") ||
                    s.source_id.startsWith("mcd_anchor_");
                  const watchlistSources = visibleSources.filter(isWatchlist);
                  const channelSources = visibleSources.filter(
                    (s) => !isWatchlist(s),
                  );
                  const renderSourceRow = (s: Source) => (
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
                              : approved.filter((id) => id !== s.source_id),
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
                        {s.reason && <small>{s.reason}</small>}
                        <small className="source-id">
                          {s.source_id} · {s.endpoint}
                        </small>
                      </span>
                      <span
                        className={`tag ${s.retirement_recommended ? "amber" : s.health_status === "quiet" ? "quiet" : ""}`}
                      >
                        {s.retirement_recommended
                          ? "建议清理"
                          : s.health_status === "quiet"
                            ? "静默正常"
                            : {
                                fixture: "模拟来源",
                                replay: "离线回放",
                                rss: "RSS",
                                manual: "手动录入",
                              }[s.type]}
                      </span>
                    </label>
                  );
                  return (
                    <div className="sources-layout">
                      <section className="section-block">
                        <div className="section-title">
                          <h2>追踪词表与信息来源</h2>
                          <button
                            disabled={busy || running}
                            onClick={() => showDialog("source")}
                          >
                            <Icon name="plus" size={15} /> 添加来源
                          </button>
                        </div>
                        <div
                          className={`watchlist-save-bar ${hasUnsavedSources ? "dirty" : ""}`}
                        >
                          <span className="watchlist-save-status">
                            {hasUnsavedSources
                              ? `有未保存的勾选变更（当前勾选 ${approved.length} 项），请点击保存后生效。`
                              : `已生效 ${approved.length} 项追踪配置。默认使用 0-Token 已有事件事实作为扩展理由。`}
                          </span>
                          <div className="watchlist-save-actions">
                            <button
                              type="button"
                              disabled={busy || running}
                              onClick={() =>
                                void action(async () => {
                                  const next = !llmSummaryOn;
                                  setLlmSummaryOn(next);
                                  await refreshSources(next);
                                  setToast(
                                    next
                                      ? "已开启 AI 对比推荐理由（单次轻量总结）。"
                                      : "已切换回 0-Token 事实依据模式。",
                                  );
                                })
                              }
                            >
                              {llmSummaryOn
                                ? "AI 对比理由：ON"
                                : "AI 对比理由：OFF (0-Token)"}
                            </button>
                            <button
                              type="button"
                              className="primary"
                              disabled={busy || running || !hasUnsavedSources}
                              onClick={() => void action(saveApprovedSources)}
                            >
                              保存批准来源 <Icon name="check" size={15} />
                            </button>
                          </div>
                        </div>
                        {llmSummaryNote && (
                          <div className="llm-summary-box">
                            <strong>AI 扩展优先级对比建议</strong>
                            <p>{llmSummaryNote}</p>
                          </div>
                        )}
                        {watchlistSources.length > 0 && (
                          <>
                            <div className="source-group-hd">
                              <span>追踪关键词与支线词表（Watchlist · 盯谁）</span>
                              <small>{watchlistSources.length} 项</small>
                            </div>
                            <div className="source-list">
                              {watchlistSources.map(renderSourceRow)}
                            </div>
                          </>
                        )}
                        {channelSources.length > 0 && (
                          <>
                            <div className="source-group-hd">
                              <span>媒体渠道与语料源（Channels · 去哪抓）</span>
                              <small>{channelSources.length} 项</small>
                            </div>
                            <div className="source-list">
                              {channelSources.map(renderSourceRow)}
                            </div>
                          </>
                        )}
                        <div className="section-actions">
                          <button
                            className="primary"
                            disabled={busy || running}
                            onClick={() => void action(saveApprovedSources)}
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
                        <h3>想测试回放语料？</h3>
                        <p className="hint">
                          单独创建回放主题（支持 LBJ 2026 与香港麦当劳 2026 真实语料），避免混入模拟新闻和实时订阅。
                        </p>
                        <button
                          className="text-button"
                          onClick={() => showDialog("replay")}
                        >
                          准备回放工作区 <Icon name="arrow" size={14} />
                        </button>
                      </aside>
                    </div>
                  );
                })()}
                {tab === "activity" && (
                  <div className="activity-layout">
                    <section className="run-list">
                      <span className="kicker">SESSIONS</span>
                      <h2>检查历史</h2>
                      {dashboard.recent_runs.map((r) => (
                        <button
                          className={`session-row ${run?.run.run_id === r.run_id ? "selected" : ""}`}
                          aria-pressed={run?.run.run_id === r.run_id}
                          disabled={busy}
                          key={r.run_id}
                          onClick={() =>
                            void action(async () => {
                              const detail = await api<RunDetail>(
                                `/runs/${r.run_id}`,
                              );
                              viewedRun.current = r.run_id;
                              setRun(detail);
                            })
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
                                  onClick={() =>
                                    void action(() =>
                                      resumeRun(run.run.run_id),
                                    )
                                  }
                                >
                                  续跑未完成证据
                                </button>
                              </div>
                              <ul>
                                {unfinished.map((item) => (
                                  <li
                                    key={`${item.source_id}:${item.external_id}`}
                                  >
                                    <strong>{item.title}</strong>{" "}
                                    <span className="meta">
                                      {item.external_id} · {status(item.status)}
                                      {" · "}
                                      {item.attempts} 次尝试
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
              </div>
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
