import { SidebarHandle, useSidebarWidth } from "./sidebar-resize";
import { SidebarTopics } from "./sidebar-topics";
import { useTopicOrder } from "./topic-order";
import { searchProviderLabel, SourceHealth } from "./source-health";
import { MAX_TOPIC_TAGS, TopicTagPicker, tagLabel, tagOptions } from "./topic-tags";
import { WorkspaceClock } from "./clock";
import { TopicReport } from "./topic-report";
import { TranslationContext, useWorkspaceTranslation } from "./reference-translation";
import { applyTheme, initialTheme, locale, setLocale, t, type Locale } from "./i18n";
import { flushSync } from "react-dom";
import { CoverImage } from "./covers";
import { Preferences } from "./preferences";
import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, json } from "./api";
import type {
  Topic,
  Source,
  Dashboard,
  Notification,
  RunDetail,
  Event as TrackedEvent,
  EventDetail,
  EventSuggestion,
  EvidenceDecision,
  ModelStatus,
  SearchStatus,
  Corpus,
} from "./types";
import {
  GoalEditor,
  RegisterSource,
  ManualEvidenceForm,
  ReviewPanel,
  ModelSettingsPanel,
  SearchSettingsPanel,
  ReplaySetup,
} from "./member-d";
import { Icon, Modal, Empty } from "./ui";
import { EventView, EvidenceView } from "./event-view";
import { OverviewSection, OverviewSummary } from "./overview";
import { BrandLink, ProjectHome, topicUrl } from "./home";
import "@fontsource-variable/space-grotesk/wght.css";
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
  topicTitle,
  topicDescription,
} from "./display";
import "./style.css";
import "./workspace-v11.css";
import "./radar.css";
import "./signal.css";
import "./operations.css";
import "./topic-management.css";

const tabs = [
  ["overview", "概览", "grid"],
  ["events", "事件", "events"],
  ["evidence", "证据库", "evidence"],
  ["review", "待审核", "review"],
  ["sources", "信息源", "sources"],
  ["activity", "运行记录", "activity"],
  ["report", "追踪报告", "evidence"],
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
  | "search"
  | "replay"
  | "delete"
  | "delete-event"
  | null;
const SEARCH_LANGUAGE_OPTIONS = [
  { code: "zh-CN", label: "简中" },
  { code: "zh-HK", label: "繁港" },
  { code: "en-US", label: "英文" },
  { code: "ko", label: "韩文" },
  { code: "ja", label: "日文" },
] as const;
const SEARCH_LANGUAGE_MAP = new Map<string, string>(
  SEARCH_LANGUAGE_OPTIONS.map((o) => [o.code, o.label]),
);
const fixtureBatches = ["1", "2", "3", "empty"];

// Old walkthrough topics are retired from the workspace without rewriting stored records.
async function loadWorkspaceTopics() {
  const items = await api<Topic[]>("/topics");
  return items.filter((topic) => !/^ExampleSoft\s*·\s*(三步演[示習]|Three-step demo)$/i.test(topic.title.trim()));
}

document.documentElement.dataset.theme = initialTheme();

function App() {
  const sidebar = useSidebarWidth();
  const [language, setLanguage] = useState(locale);
  const [theme, setTheme] = useState(initialTheme);
  const [translateContent, setTranslateContent] = useState(() => localStorage.getItem("traceradar-content-translation") === "on");
  function toggleTranslation(value: boolean) {
    localStorage.setItem("traceradar-content-translation", value ? "on" : "off");
    setTranslateContent(value);
  }
  const preferences = <><WorkspaceClock /><Preferences language={language} theme={theme}
    onLanguage={(value: Locale) => { setLocale(value); setLanguage(value); toggleTranslation(true); }}
    onTheme={(value) => { applyTheme(value); setTheme(value); }} /></>;

  const [topics, setTopics] = useState<Topic[]>([]),
    [selected, setSelected] = useState(""),
    [tab, setTab] = useState<Tab>("overview");
  const { orderedTopics, pinned, togglePin, moveTopic, forgetTopic } = useTopicOrder(topics);
  const orderedTopicsRef = useRef(orderedTopics);
  orderedTopicsRef.current = orderedTopics;
  const [dashboard, setDashboard] = useState<Dashboard | null>(null),
    [sources, setSources] = useState<Source[]>([]),
    [approved, setApproved] = useState<string[]>([]);
  const [evidence, setEvidence] = useState<EvidenceDecision[]>([]),
    [notices, setNotices] = useState<Notification[]>([]),
    [run, setRun] = useState<RunDetail | null>(null);
  const [event, setEvent] = useState<EventDetail | null>(null),
    [evidenceId, setEvidenceId] = useState(""),
    [model, setModel] = useState<ModelStatus | null>(null),
    [searchSettings, setSearchSettings] = useState<SearchStatus | null>(null),
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
  const [llmMode, setLlmMode] = useState<string | null>(null);
  const [searchLanguages, setSearchLanguages] = useState<string[]>([]);
  const [lastLlmLangs, setLastLlmLangs] = useState<string>("");
  const [recommendedLanguages, setRecommendedLanguages] = useState<string[]>(
    [],
  );
  const [llmUsage, setLlmUsage] = useState<{
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  } | null>(null);
  const [eventSuggestions, setEventSuggestions] = useState<EventSuggestion[]>(
    [],
  );
  const [editingSource, setEditingSource] = useState<{
    source_id: string;
    name: string;
    type: Source["type"];
    query: string;
    endpoint: string;
    search_language: string;
    fetch_full_text: boolean;
    registered: boolean;
    config: Record<string, unknown>;
  } | null>(null);
  const [newTitle, setNewTitle] = useState(""),
    [newDescription, setNewDescription] = useState("");
  const [newTags, setNewTags] = useState<string[]>([]);
  const [deleteTarget, setDeleteTarget] = useState<Topic>();
  const [deleteEventTarget, setDeleteEventTarget] = useState<TrackedEvent>();
  const [deleteEventDisposition, setDeleteEventDisposition] = useState<"irrelevant" | "unwanted">("irrelevant");
  const [deleteEventReason, setDeleteEventReason] = useState("");
  const [deleteEventAddExclusion, setDeleteEventAddExclusion] = useState(true);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const knownTopics = useRef(topics);
  knownTopics.current = topics;
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
    (s) => s.health_status === "candidate" && !approved.includes(s.source_id),
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
    if (id !== tab) setQuery("");
    setTab(id);
    if (focus) {
      requestAnimationFrame(() => {
        const panel = document.getElementById("workspace-panel");
        panel?.focus({ preventScroll: true });
        document.querySelector(".tabbar")?.scrollIntoView({ block: "start" });
      });
    }
  }

  function selectTopic(id: string, fromHistory = false) {
    if (id === selectedRef.current) {
      setQuery("");
      setTab("overview");
      return;
    }
    if (!fromHistory) history.pushState(null, "", topicUrl(id));
    selectedRef.current = id;
    setSelected(id);
    setDashboard(null);
    setLoading(!!id);
    setRun(null);
    viewedRun.current = "";
    setEvidence([]);
    setSources([]);
    setApproved([]);
    setNotices([]);
    setReplayCorpus("");
    setAsOf("");
    setLlmSummaryOn(false);
    setLlmSummaryNote(null);
    setLlmMode(null);
    setSearchLanguages([]);
    setLastLlmLangs("");
    setRecommendedLanguages([]);
    setLlmUsage(null);
    setEventSuggestions([]);
    setEditingSource(null);
    setWatchId("");
    setEvent(null);
    setEvidenceId("");
    setQuery("");
    setShowHistorySources(false);
    setTab("overview");
    setError("");
    requestAnimationFrame(() => {
      window.scrollTo({ top: 0, left: 0, behavior: "instant" });
      document.getElementById(id ? "topic-title" : "home-title")?.focus({ preventScroll: true });
    });
  }
  function navigateTopic(id: string) {
    const oldId = selectedRef.current;
    if (oldId === id || !document.startViewTransition ||
        matchMedia("(prefers-reduced-motion: reduce)").matches) {
      selectTopic(id);
      return;
    }
    const coverSelector = `[data-topic-cover="${CSS.escape(id || oldId)}"]`;
    const oldCover = document.querySelector<HTMLElement>(coverSelector);
    if (oldCover) oldCover.style.viewTransitionName = "topic-cover";
    const transition = document.startViewTransition(() => {
      if (oldCover) oldCover.style.viewTransitionName = "";
      flushSync(() => selectTopic(id));
      if (!id) document.querySelector(coverSelector)?.closest("li")?.scrollIntoView({ block: "nearest", inline: "center" });
      const newCover = document.querySelector<HTMLElement>(coverSelector);
      if (newCover) newCover.style.viewTransitionName = "topic-cover";
    });
    const clearTransition = () => {
      const cover = document.querySelector<HTMLElement>(coverSelector);
      if (cover) cover.style.viewTransitionName = "";
    };
    void transition.finished.then(clearTransition, clearTransition);
  }

  useEffect(() => {
    function restoreRoute() {
      const id = new URLSearchParams(location.search).get("topic");
      selectTopic(knownTopics.current.find((topic) => topic.topic_id === id)?.topic_id ?? "", true);
    }
    window.addEventListener("popstate", restoreRoute);
    return () => window.removeEventListener("popstate", restoreRoute);
  }, []);
  useEffect(() => {
    let disposed = false;
    void Promise.all([
      loadWorkspaceTopics(),
      api<{ status: string }>("/health"),
      api<ModelStatus>("/settings/model"),
      api<SearchStatus>("/settings/search").catch(() => null),
      api<Corpus[]>("/replay/corpora"),
    ])
      .then(([items, h, m, ss, c]) => {
        if (disposed) return;
        setTopics(items);
        setHealth(h.status === "ok");
        setModel(m);
        setSearchSettings(ss);
        setCorpora(c);
        const requested = new URLSearchParams(location.search).get("topic");
        if (requested && items.some((topic) => topic.topic_id === requested)) {
          selectTopic(requested, true);
        } else {
          if (requested) history.replaceState(null, "", topicUrl(""));
          setLoading(false);
        }
      })
      .catch((e) => {
        if (disposed) return;
        setError(String(e));
        setLoading(false);
      });
    return () => { disposed = true; };
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
        const selectedCorpus = availableCorpora[0];
        setReplayCorpus(selectedCorpus?.corpus_id ?? "");
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

  async function refresh(
    sourceSettingsChanged = false,
    options: { includeRecommendations?: boolean } = {},
  ) {
    const id = selected;
    const includeRec = options.includeRecommendations ?? true;
    const [d, n, e, rec] = await Promise.all([
      api<Dashboard>(`/topics/${id}/dashboard`),
      api<Notification[]>(`/topics/${id}/notifications`),
      api<EvidenceDecision[]>(`/topics/${id}/evidence`),
      includeRec
        ? api<{ sources: Source[] }>(
            `/topics/${id}/source-recommendations`,
            json(
              "POST",
              searchLanguages.length
                ? { search_languages: searchLanguages }
                : {},
            ),
          ).catch(() => null)
        : Promise.resolve(null),
    ]);
    if (selectedRef.current !== id) return;
    setDashboard(d);
    setNotices(n);
    setEvidence(e);
    if (rec) {
      setSources(rec.sources);
      if (!sourceSettingsChanged) {
        const validIds = new Set([
          ...rec.sources.map((x) => x.source_id),
          ...d.approved_sources.map((x) => x.source_id),
        ]);
        setApproved((prev) => prev.filter((sid) => validIds.has(sid)));
      }
      setLlmSummaryOn(false);
      setLlmSummaryNote(null);
      setLlmMode(null);
      setLastLlmLangs("");
      setRecommendedLanguages([]);
      setLlmUsage(null);
    }
    if (sourceSettingsChanged) {
      setApproved(d.approved_sources.map((s) => s.source_id));
      setProvider(fixtureOnly(d.approved_sources) ? "mock" : "qwen");
      const activeReplay = d.approved_sources.filter(
        (s) => s.type === "replay",
      );
      const nextCorpus = corpora.find((c) =>
        activeReplay.some((s) => s.config.corpus_path === c.corpus_path),
      );
      const nextCorpusId = nextCorpus?.corpus_id ?? "";
      const corpusChanged = nextCorpusId !== replayCorpus;
      setReplayCorpus(nextCorpusId);
      if (corpusChanged) {
        setAsOf(
          nextCorpus?.cutoffs[0] ? localInput(nextCorpus.cutoffs[0]) : "",
        );
      } else if (!asOf && nextCorpus?.cutoffs[0]) {
        setAsOf(localInput(nextCorpus.cutoffs[0]));
      }
      setBatch(nextCorpus ? "all" : "1");
    }
  }
  async function refreshSources(
    wantLlmSummary = llmSummaryOn,
    langsOverride?: string[],
  ) {
    const id = selected;
    const activeLangs = langsOverride ?? searchLanguages;
    const body: Record<string, unknown> = { llm_summary: wantLlmSummary };
    if (activeLangs.length) body.search_languages = activeLangs;
    const s = await api<{
      sources: Source[];
      llm_summary_enabled?: boolean;
      llm_mode?: string | null;
      llm_summary_note?: string | null;
      llm_summary_error?: string | null;
      recommended_languages?: string[];
      event_suggestions?: EventSuggestion[];
      llm_usage?: {
        prompt_tokens?: number;
        completion_tokens?: number;
        total_tokens?: number;
      } | null;
    }>(`/topics/${id}/source-recommendations`, json("POST", body));
    if (selectedRef.current !== id) return null;
    setSources(s.sources);
    const validIds = new Set([
      ...s.sources.map((x) => x.source_id),
      ...(dashboard?.approved_sources.map((x) => x.source_id) ?? []),
    ]);
    setApproved((prev) => prev.filter((sid) => validIds.has(sid)));
    const enabled = Boolean(s.llm_summary_enabled);
    setLlmSummaryOn(enabled);
    setLlmMode(s.llm_mode ?? null);
    setLlmSummaryNote(s.llm_summary_note ?? null);
    setLastLlmLangs(enabled ? activeLangs.join(",") : "");
    setRecommendedLanguages(s.recommended_languages ?? []);
    setLlmUsage(s.llm_usage ?? null);
    setEventSuggestions(s.event_suggestions ?? []);
    return s;
  }
  async function saveApprovedSources() {
    const toRegister = sources.filter(
      (s) => approved.includes(s.source_id) && s.registered === false,
    );
    for (const s of toRegister) {
      await api(
        "/sources",
        json("POST", {
          source_id: s.source_id,
          name: s.name.slice(0, 200),
          type: s.type,
          endpoint: s.endpoint,
          config: s.config,
        }),
      );
    }
    await api(
      `/topics/${selected}/sources`,
      json("PUT", { approved_source_ids: approved }),
    );
    await refresh(true);
    setTopics(await loadWorkspaceTopics());
    setToast(t("已保存这个主题的追踪词表与信息来源。"));
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
        const stillActive = active(detail.run.status);
        await refresh(false, { includeRecommendations: !stillActive });
        retries = 0;
        if (!disposed) {
          if (stillActive) timer = setTimeout(poll, 900);
          else {
            setWatchId("");
            setToast(
              detail.run.status === "completed"
                ? t("检查已完成，工作区已更新。")
                : t("检查已结束。请在运行记录中查看处理结果。"),
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
      throw Error(t("请先填写模型服务配置。"));
    }
    if (replay && !manual && !replaySource)
      throw Error(t("请选择可用的回放语料。"));
    if (replay && !manual && !cutoff) throw Error(t("请选择回放截止时间。"));
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
        ? t("新闻已提交，正在处理。")
        : t("检查已启动，可在运行记录中查看进度。"),
    );
    await refresh();
  }
  async function openEvent(id: string) {
    const topicId = selected;
    await action(async () => {
      const detail = await api<EventDetail>(`/events/${id}`);
      if (selectedRef.current === topicId) setEvent(detail);
    });
  }
  async function removeExclusion(rule: string) {
    if (!dashboard) return;
    const next = (dashboard.topic.exclusions ?? []).filter((x) => x !== rule);
    await action(async () => {
      await api(`/topics/${selected}`, json("PATCH", { exclusions: next }));
      await refresh();
      setTopics(await loadWorkspaceTopics());
      setToast(t("已撤回该排除规则。"));
    });
  }
  async function saveEditedSource() {
    if (!editingSource) return;
    await action(async () => {
      const cleanName = editingSource.name.trim();
      const nextConfig: Record<string, unknown> = { ...editingSource.config };
      let nextEndpoint = editingSource.endpoint.trim();
      if (editingSource.type === "web_search") {
        const cleanQuery = editingSource.query.trim();
        if (!cleanQuery) throw Error(t("搜索关键词不能为空。"));
        nextConfig.query = cleanQuery;
        nextConfig.fetch_full_text = editingSource.fetch_full_text;
        if (editingSource.search_language) {
          nextConfig.search_language = editingSource.search_language;
        } else {
          delete nextConfig.search_language;
        }
        nextEndpoint = `search://${encodeURIComponent(cleanQuery).slice(0, 800)}`;
      } else if (editingSource.type === "rss") {
        nextConfig.fetch_full_text = editingSource.fetch_full_text;
      }
      if (editingSource.registered) {
        await api(
          `/sources/${editingSource.source_id}`,
          json("PATCH", {
            name: cleanName || editingSource.source_id,
            endpoint: nextEndpoint,
            config: nextConfig,
          }),
        );
      } else {
        await api(
          "/sources",
          json("POST", {
            source_id: editingSource.source_id,
            name: cleanName || editingSource.source_id,
            type: editingSource.type,
            endpoint: nextEndpoint,
            config: nextConfig,
          }),
        );
      }
      setEditingSource(null);
      await refreshSources(false);
      await refresh();
      setToast(t("来源配置已更新，下次检查生效。"));
    });
  }
  async function applyEventSuggestion(sug: EventSuggestion) {
    await action(async () => {
      if (sug.action === "dismiss") {
        await api(
          `/events/${sug.event_id}/dismiss`,
          json("POST", {
            disposition: "irrelevant",
            reason: sug.reason,
            add_exclusion: true,
          }),
        );
        setToast(t("已按建议移除误报事件并记入主题排除规则。"));
      } else if (sug.action === "merge" && sug.target_event_id) {
        await api(
          `/events/${sug.event_id}/merge`,
          json("POST", {
            target_event_id: sug.target_event_id,
            reason: sug.reason,
          }),
        );
        setToast(t("已按建议合并重复事件。"));
      }
      setEventSuggestions((prev) =>
        prev.filter((item) => item.event_id !== sug.event_id),
      );
      await refresh();
      setTopics(await loadWorkspaceTopics());
    });
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
  function openNewTopic(tag?: string, example?: string) {
    if (tag) setNewTags((tags) => tags.includes(tag) || tags.length >= MAX_TOPIC_TAGS ? tags : [...tags, tag]);
    if (example) setNewTitle(example);
    showDialog("new");
  }
  function requestDelete(topic: Topic) {
    setDeleteTarget(topic);
    showDialog("delete");
  }
  function confirmDelete() {
    if (!deleteTarget) return;
    const id = deleteTarget.topic_id;
    void action(async () => {
      try {
        await api(`/topics/${id}`, { method: "DELETE" });
      } catch (error) {
        if (error instanceof Error && error.message.startsWith("TOPIC_RUN_ACTIVE:")) {
          throw new Error(t("检查仍在运行，结束后才能删除主题。"));
        }
        if (!(error instanceof Error && error.message.startsWith("NOT_FOUND:"))) throw error;
      }
      setTopics((items) => items.filter((topic) => topic.topic_id !== id));
      forgetTopic(id);
      if (selectedRef.current === id) {
        const next = orderedTopicsRef.current.find((topic) => topic.topic_id !== id)?.topic_id ?? "";
        history.replaceState(null, "", topicUrl(next));
        selectTopic(next, true);
      }
      setDialog(null);
      setDeleteTarget(undefined);
      setToast(t("主题已删除。"));
    });
  }

  function confirmDeleteEvent() {
    if (!deleteEventTarget) return;
    const { event_id: id, topic_id: topicId } = deleteEventTarget;
    const useDismiss = deleteEventAddExclusion || Boolean(deleteEventReason.trim());
    void action(async () => {
      try {
        if (useDismiss) {
          await api(
            `/events/${id}/dismiss`,
            json("POST", {
              disposition: deleteEventDisposition,
              reason: deleteEventReason.trim() || undefined,
              add_exclusion: deleteEventAddExclusion,
            }),
          );
        } else {
          await api(`/events/${id}`, { method: "DELETE" });
        }
      } catch (error) {
        if (error instanceof Error && error.message.startsWith("TOPIC_RUN_ACTIVE:")) {
          throw new Error(t("检查仍在运行，结束后才能删除事件。"));
        }
        if (!(error instanceof Error && error.message.startsWith("NOT_FOUND:"))) throw error;
      }
      setDeleteEventTarget(undefined);
      setDeleteEventReason("");
      setDeleteEventAddExclusion(true);
      setDeleteEventDisposition("irrelevant");
      setDialog(null);
      if (selectedRef.current !== topicId) return;
      setDashboard((value) => value && { ...value, events: value.events.filter((item) => item.event_id !== id) });
      setNotices((value) => value.filter((item) => item.event_id !== id));
      setEvent((value) => value?.event.event_id === id ? null : value);
      setEventSuggestions((prev) => prev.filter((item) => item.event_id !== id));
      setToast(
        deleteEventAddExclusion
          ? t("已移除事件并加入主题排除规则，原新闻不会重复建档。")
          : t("事件已删除。"),
      );
      await refresh(false, { includeRecommendations: false });
      setTopics(await loadWorkspaceTopics());
    });
  }

  const { lookup: tr, failed: translationFailed } = useWorkspaceTranslation(language, [
    ...topics.flatMap((item) => [item.title, item.description]),
    ...(dashboard?.events ?? []).flatMap((item) => [item.title, item.state.current_status, item.state.key_facts.at(-1)?.fact ?? ""]),
    ...(event ? [event.event.title, event.event.state.current_status,
      ...event.event.state.key_facts.map((item) => item.fact),
      ...event.event.state.timeline.map((item) => item.description),
      ...event.history.flatMap((item) => item.impact_reason ? [item.impact_reason] : [])] : []),
    ...evidence.slice(0, 40).map((item) => item.evidence.title),
  ], translateContent && !!model?.configured);

  const selectedEvidence = evidence.find(
    (e) => e.evidence.evidence_id === evidenceId,
  );
  const search = query.trim().toLowerCase();
  const events = (dashboard?.events ?? []).filter((e) =>
    `${e.title} ${tr(eventTitle(e.title))} ${e.state.current_status} ${tr(status(e.state.current_status))} ${e.state.key_facts.map((fact) => text(fact.fact)).join(" ")}`
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
  function renderEventList(limit?: number) {
    const visibleEvents = limit
      ? [...events].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at)).slice(0, limit)
      : events;
    return (
      <div className="event-table">
        {events.length ? (
          visibleEvents.map((e) => (
            <div className="event-list-item" key={e.event_id}>
              <button type="button" className="event-row" disabled={busy}
                onClick={() => void openEvent(e.event_id)}>
                <span className="event-row-main">
                  <strong>{tr(eventTitle(e.title))}</strong>
                  <span>{tr(text(e.state.key_facts.at(-1)?.fact ?? ""))}</span>
                </span>
                <span className={`tag ${e.state.current_status === "patched" ? "green" : ""}`}>
                  {tr(status(e.state.current_status))}
                </span>
                <span className="event-row-meta">
                  {e.state.timeline.length} {t("条进展")}
                </span>
                <Icon name="arrow" size={17} />
              </button>
              {limit === undefined && (
                <button type="button" className="event-delete" disabled={busy || running}
                  aria-label={t("删除事件 {0}", tr(eventTitle(e.title)))} title={t("删除事件")}
                  onClick={() => { setDeleteEventTarget(e); showDialog("delete-event"); }}>
                  <Icon name="trash" size={17} />
                </button>
              )}
            </div>
          ))
        ) : (
          <Empty
            title={search ? t("没有符合筛选条件的事件") : t("事件会在这里逐步形成")}
            detail={
              search
                ? t("试试事件名称、状态或事实中的其他关键词。")
                : t("批准来源并运行检查。系统会按独立事项组织事件。")
            }
          />
        )}
      </div>
    );
  }

  const runControls = (
    <aside className="run-panel">
      <div className="row-between">
        <span className="kicker">TRACKING SESSION</span>
        <span className={`status-dot ${running ? "working" : ""}`} />
      </div>
      <h3>{replay ? t("按时间回放") : t("检查新进展")}</h3>
      <p className="hint">
        {replay
          ? t("每次只读取截止时间之前的新闻。")
          : t("读取已批准来源，判断是否有值得记录的变化。")}
      </p>
      <label>
        {t("分析方式")}
        <select
          value={provider}
          disabled={busy || running}
          onChange={(e) => setProvider(e.target.value as "mock" | "qwen")}
        >
          <option
            value="mock"
            disabled={!fixtureOnly(dashboard?.approved_sources ?? [])}
          >
            {t("模拟来源")}
          </option>
          <option value="qwen">{t("真实模型分析")}</option>
        </select>
      </label>
      {provider === "qwen" && (
        <p className="model-note">
          {model?.configured ? model.model : t("尚未配置模型")}{" "}
          <button
            className="text-button"
            onClick={() => showDialog("settings")}
          >
            {model?.configured ? t("更改") : t("去配置")}{" "}
            <Icon name="arrow" size={12} />
          </button>
        </p>
      )}
      {fixture && (
        <label>
          {t("新闻批次")}
          <select
            value={batch}
            onChange={(e) => setBatch(e.target.value)}
            disabled={busy || running}
          >
            {fixtureBatches.map((id) => (
              <option key={id} value={id}>{id === "empty" ? t("空批次") : t("批次 {0}", id)}</option>
            ))}
          </select>
        </label>
      )}
      {replay && (
        <>
          <label>
            {t("回放语料")}
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
                  {c.name} · {c.records} {t("条")}
                </option>
              ))}
            </select>
          </label>
          <p className="hint">
            {t("本次只读取选定的离线来源，避免混入模拟新闻、实时 RSS 或重复语料。")}
          </p>
          <label>
            {t("回放截止 · 本地时间")}
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
                <span>{t("阶段")} {i + 1}</span>
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
          ? t("正在检查…")
          : provider === "qwen" && !model?.configured
            ? t("打开模型配置")
            : t("开始检查")}
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
        {manualAvailable ? t("手动提交新闻") : t("管理手动来源")}
      </button>
      <div className="run-footnote">
        {replaySources.length ? replaySources.length : (dashboard?.approved_sources.length ?? 0)}{" "}
        {t("个本次来源 ·")} {replay ? t("离线回放") : t("手动触发")}
        <br />
        {latest
          ? t("最近检查 {0}", date(latest.completed_at ?? latest.started_at))
          : t("尚未运行")}
      </div>
    </aside>
  );

  const feedback = (
    <div className="workspace-feedback" hidden={!(error && !dialog) && !toast}>
      {error && !dialog && (
        <div role="alert" className="error">
          {error}
          <button
            className="icon-button"
            aria-label={t("关闭错误提示")}
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
    </div>
  );

  const translationToggle = (
    <button type="button" className="translation-switch" disabled={!model?.configured}
      title={!model?.configured || translationFailed ? t("翻译暂不可用，请检查模型配置后重试。") : t("机器参考译文")}
      aria-pressed={translateContent && !!model?.configured}
      onClick={() => toggleTranslation(!translateContent)}>
      {t(translationFailed ? "翻译未完成" : translateContent && model?.configured ? "显示原文" : "翻译当前内容")}
    </button>
  );
  const currentTopic = dashboard?.topic ?? topics.find((topic) => topic.topic_id === selected);
  const workspaceView = (
    <>
      <aside className="sidebar">
        <BrandLink onHome={() => navigateTopic("")} />
        <div className="sidebar-section-label topic-label">
          {t("追踪主题")}
          <button
            className="icon-button"
            disabled={busy}
            aria-label={t("新建追踪主题")}
            onClick={() => openNewTopic()}
          >
            <Icon name="plus" size={15} />
          </button>
        </div>
        <SidebarTopics topics={orderedTopics} selected={selected} pinned={pinned} busy={busy}
          onSelect={selectTopic} onPin={togglePin} onMove={moveTopic} />
        <button className="sidebar-replay" onClick={() => showDialog("replay")}>
          <Icon name="play" size={14} />
          <span>{t("准备新闻回放")}</span>
        </button>
        <div className="sidebar-bottom">
          <button aria-label={t("模型配置")} onClick={() => showDialog("settings")}>
            <Icon name="settings" />
            {t("模型配置")}
            <span className={`tiny-dot ${model?.configured ? "online" : ""}`} />
          </button>
          <div className="sidebar-status">
            <span className={`status-dot ${health ? "online" : ""}`} />
            {health
              ? t("本地服务在线")
              : loading
                ? t("正在连接本地服务")
                : t("本地服务不可用")}
          </div>
        </div>
      </aside>
      <SidebarHandle {...sidebar} />
      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <a href={topicUrl("")} onClick={(e) => {
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
              e.preventDefault();
              navigateTopic("");
            }}>{t("追踪主题")}</a>
          </div>
          <div className="topbar-actions">
            {preferences}
            {translationToggle}
            <button
              className="icon-button mobile-replay"
              aria-label={t("准备语料回放")}
              onClick={() => showDialog("replay")}
            >
              <Icon name="play" />
            </button>
            <button
              className="icon-button mobile-new"
              aria-label={t("新建主题")}
              disabled={busy}
              onClick={() => openNewTopic()}
            >
              <Icon name="plus" />
            </button>
          </div>
        </header>
        <main>
          <div className="page-heading topic-heading">
            {currentTopic && <CoverImage topic={currentTopic} className="topic-banner" />}
            <div>
              <h1 id="topic-title" tabIndex={-1}>
                {currentTopic ? tr(topicTitle(currentTopic.title)) : t("追踪主题")}
              </h1>
              {currentTopic && <p>{tr(topicDescription(currentTopic.description))}</p>}
              {!!currentTopic?.tags?.length && <div className="frame-tags">{currentTopic.tags.map((tag) => <span key={tag}>{tagLabel(tag)}</span>)}</div>}
            </div>
            {dashboard && (
              <div className="topic-heading-actions">
                <button className="heading-edit" type="button" disabled={busy || running} onClick={() => showDialog("goal")}>
                  <span>{t("编辑追踪目标")}</span><Icon name="arrow" size={15} />
                </button>
                <button className="heading-edit" type="button" disabled={busy || running}
                  onClick={() => requestDelete(dashboard.topic)}>
                  <span>{t("删除主题")}</span><Icon name="trash" size={15} />
                </button>
              </div>
            )}
          </div>
          {feedback}
          {loading && <p className="loading">{t("正在读取工作区…")}</p>}
          {dashboard && (
            <>
              <div className="topic-meta">
                <span
                  className={`tag ${dashboard.topic.status === "active" ? "green" : ""}`}
                >
                  <span className="tiny-dot online" />
                  {status(dashboard.topic.status)}
                </span>
                <span>{dashboard.approved_sources.length} {t("个来源")}</span>
                <span>
                  {t("最近更新")}{" "}
                  {date(latest?.completed_at ?? dashboard.topic.updated_at)}
                </span>
              </div>
              <div
                className="tabbar"
                ref={tabStrip}
                role="tablist"
                aria-label={t("主题视图")}
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
                    {t(label)}
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
              {(tab === "events" || tab === "evidence") && (
                <label className="search-box">
                  <Icon name="search" size={17} />
                  <input
                    type="search"
                    aria-label={tab === "evidence" ? t("筛选证据") : t("筛选事件")}
                    placeholder={
                      tab === "evidence"
                        ? t("筛选标题、来源或内容")
                        : t("筛选事件、状态或事实")
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
                            label: t("追踪事件"),
                            open: () => openTab("events", true),
                          },
                          {
                            value: currentEvidence.length,
                            label: t("当前来源证据"),
                            open: () => openTab("evidence", true),
                          },
                          {
                            value: dashboard.reviews.length,
                            label: t("待审核"),
                            open: () => openTab("review", true),
                          },
                        ]}
                      />
                      <div className="overview-sections">
                        <OverviewSection
                          title={t("近期事件")}
                          label={t("查看全部事件")}
                          onOpen={() => openTab("events", true)}
                        >
                          {renderEventList(3)}
                        </OverviewSection>
                        <OverviewSection
                          title={t("变化与提醒")}
                          label={t("查看审核队列")}
                          onOpen={() => openTab("review", true)}
                        >
                          {!!dashboard.reviews.length && (
                            <p className="review-callout">
                              {dashboard.reviews.length} {t("条证据等待人工判断")}
                            </p>
                          )}
                          {!notices.length ? (
                            <p className="blank-line">
                              {t("有新的状态变化后，提醒会出现在这里。")}
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
                                      )?.title ?? t("事件更新"),
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
                    </div>
                    {runControls}
                  </div>
                )}
                {tab === "events" && (
                  <section className="section-block">
                    <div className="section-title">
                      <h2>{t("全部事件")}</h2>
                      <span className="meta">
                        {t("选择一行查看事实、时间线与依据（弹窗内可改标题、合并或移除）")}
                      </span>
                    </div>
                    {!!dashboard.topic.exclusions?.length && (
                      <div className="exclusion-bar">
                        <span className="exclusion-bar-label">{t("已生效排除规则：")}</span>
                        <div className="exclusion-pills">
                          {dashboard.topic.exclusions.map((rule) => (
                            <span className="exclusion-pill" key={rule}>
                              <span>{rule}</span>
                              <button
                                type="button"
                                disabled={busy || running}
                                aria-label={t("移除排除规则")}
                                onClick={() => void removeExclusion(rule)}
                              >
                                ×
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                    {eventSuggestions.length > 0 && (
                      <div className="ai-event-suggestions">
                        <strong>{t("AI 审计发现以下疑似异常事件（需人工确认）")}</strong>
                        {eventSuggestions.map((sug) => (
                          <div className="ai-event-suggestion-row" key={`${sug.action}-${sug.event_id}`}>
                            <span>
                              <b>
                                {sug.action === "merge"
                                  ? t("建议合并：「{0}」→「{1}」", sug.event_title, sug.target_event_title ?? "")
                                  : t("建议移除：「{0}」", sug.event_title)}
                              </b>
                              <small>{sug.reason}</small>
                            </span>
                            <div className="action-row">
                              <button
                                type="button"
                                disabled={busy || running}
                                onClick={() => void applyEventSuggestion(sug)}
                              >
                                {sug.action === "merge" ? t("确认合并") : t("移出并记入排除")}
                              </button>
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() =>
                                  setEventSuggestions((prev) =>
                                    prev.filter((x) => x.event_id !== sug.event_id),
                                  )
                                }
                              >
                                {t("忽略")}
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                    {renderEventList()}
                  </section>
                )}
                {tab === "evidence" && (
                  <section className="section-block">
                    <div className="section-title">
                      <h2>{t("证据库")}</h2>
                      <span className="meta">{t("包括被排除、重复与待审的信息")}</span>
                    </div>
                    <label className="check-row">
                      <input
                        type="checkbox"
                        checked={showHistorySources}
                        onChange={(e) =>
                          setShowHistorySources(e.target.checked)
                        }
                      />
                      {t("显示全部来源的历史记录")}
                    </label>
                    {!evidenceRows.length ? (
                      <Empty
                        title={
                          search ? t("没有符合筛选条件的证据") : t("还没有读入的新闻")
                        }
                        detail={
                          search
                            ? t("试试其他关键词，或查阅全部来源的历史记录。")
                            : t("运行一次检查，或手动提交一条新闻。")
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
                              ? t("待审核")
                              : ["failed", "failed_retryable", "pending"].includes(v.status)
                                ? status(v.status)
                                : duplicate
                                  ? t("重复报道")
                                  : v.assessment?.is_relevant === false
                                    ? t("已排除")
                                    : t("已处理");
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
                                <strong>{tr(headline(v.evidence.title))}</strong>
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
                      setToast(t("审核已记录，事件与历史已刷新。"));
                    }}
                  />
                )}
                {tab === "sources" && (() => {
                  const visibleSources = sources.filter((s) => {
                    const slug = selected.slice(0, 8).toLowerCase();
                    if (
                      s.source_id.startsWith("gnews_") &&
                      s.source_id !== `gnews_${slug}` &&
                      !approved.includes(s.source_id)
                    ) {
                      return false;
                    }
                    if (
                      s.source_id.startsWith("websearch_") &&
                      s.source_id !== `websearch_${slug}` &&
                      !approved.includes(s.source_id)
                    ) {
                      return false;
                    }
                    return true;
                  });
                  const isWatchlist = (s: Source) =>
                    s.role === "anchor" ||
                    s.role === "branch" ||
                    s.source_id.startsWith("kw_") ||
                    s.source_id.startsWith("websearch_") ||
                    s.source_id.startsWith("anchor_");
                  const watchlistSources = visibleSources.filter(isWatchlist);
                  const channelSources = visibleSources.filter(
                    (s) => !isWatchlist(s),
                  );
                  const renderSourceRow = (s: Source) => {
                    const isApproved = approved.includes(s.source_id);
                    const isDimmed = !isApproved && s.ai_verdict === "skip";
                    const editable =
                      s.type === "web_search" ||
                      s.type === "webpage" ||
                      s.type === "rss";
                    const isEditing = editingSource?.source_id === s.source_id;
                    return (
                    <div className="source-row-wrap" key={s.source_id}>
                    <label
                      className={`source-row ${isApproved ? "checked" : ""} ${isDimmed ? "dimmed" : ""}`}
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
                            s.type === "rss" ||
                            s.type === "web_search" ||
                            s.type === "webpage"
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
                      <span className="source-tags">
                        {editable && (
                          <button
                            type="button"
                            className="inline-edit-btn"
                            disabled={busy || running}
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              if (isEditing) {
                                setEditingSource(null);
                                return;
                              }
                              const cfg = (s.config ?? {}) as Record<string, unknown>;
                              setEditingSource({
                                source_id: s.source_id,
                                name: s.name,
                                type: s.type,
                                query: typeof cfg.query === "string" ? cfg.query : "",
                                endpoint: s.endpoint,
                                search_language:
                                  typeof cfg.search_language === "string"
                                    ? cfg.search_language
                                    : "",
                                fetch_full_text: cfg.fetch_full_text !== false,
                                registered: s.registered !== false,
                                config: cfg,
                              });
                            }}
                          >
                            {isEditing ? t("收起") : t("改词")}
                          </button>
                        )}
                        {!isApproved && s.ai_verdict === "approve" && (
                          <span className="tag ai-pick">{t("AI 推荐")}</span>
                        )}
                        {!isApproved && s.ai_verdict === "skip" && (
                          <span className="tag quiet">{t("AI 建议跳过")}</span>
                        )}
                        {typeof s.config?.search_language === "string" &&
                          SEARCH_LANGUAGE_MAP.has(s.config.search_language) && (
                            <span className="tag lang">
                              {t(SEARCH_LANGUAGE_MAP.get(s.config.search_language) ?? s.config.search_language)}
                            </span>
                          )}
                        <span
                          className={`tag ${s.retirement_recommended ? "amber" : s.health_status === "quiet" || s.health_status === "archived" ? "quiet" : ""}`}
                        >
                          {s.retirement_recommended
                            ? t("建议清理")
                            : s.health_status === "quiet"
                              ? t("静默正常")
                              : s.health_status === "archived"
                                ? t("已归档")
                                : {
                                    fixture: t("模拟来源"),
                                    replay: t("离线回放"),
                                    rss: "RSS",
                                    web_search: t("全网搜索"),
                                    webpage: t("网页监控"),
                                    manual: t("手动录入"),
                                  }[s.type]}
                        </span>
                      </span>
                    </label>
                    {isEditing && editingSource && (
                      <form
                        className="source-edit-panel form-stack"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void saveEditedSource();
                        }}
                      >
                        <label>
                          {t("显示名称")}
                          <input
                            required
                            maxLength={200}
                            value={editingSource.name}
                            onChange={(e) =>
                              setEditingSource({ ...editingSource, name: e.target.value })
                            }
                          />
                        </label>
                        {editingSource.type === "web_search" ? (
                          <>
                            <label>
                              {t("搜索关键词")}
                              <input
                                required
                                maxLength={200}
                                value={editingSource.query}
                                onChange={(e) =>
                                  setEditingSource({ ...editingSource, query: e.target.value })
                                }
                              />
                            </label>
                            <label>
                              {t("检索语种")}
                              <select
                                value={editingSource.search_language}
                                onChange={(e) =>
                                  setEditingSource({
                                    ...editingSource,
                                    search_language: e.target.value,
                                  })
                                }
                              >
                                <option value="">{t("自动判断")}</option>
                                {SEARCH_LANGUAGE_OPTIONS.map((opt) => (
                                  <option key={opt.code} value={opt.code}>
                                    {t(opt.label)} ({opt.code})
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="check-row">
                              <input
                                type="checkbox"
                                checked={editingSource.fetch_full_text}
                                onChange={(e) =>
                                  setEditingSource({
                                    ...editingSource,
                                    fetch_full_text: e.target.checked,
                                  })
                                }
                              />
                              {t("仅对新文章并发抓取正文（web_fetch，失败自动回退摘要）")}
                            </label>
                          </>
                        ) : (
                          <label>
                            {editingSource.type === "webpage" ? t("目标网页 URL") : t("订阅地址")}
                            <input
                              type="url"
                              required
                              value={editingSource.endpoint}
                              onChange={(e) =>
                                setEditingSource({ ...editingSource, endpoint: e.target.value })
                              }
                            />
                          </label>
                        )}
                        <div className="action-row">
                          <button className="primary" disabled={busy || running}>
                            {t("保存修改")}
                          </button>
                          <button type="button" onClick={() => setEditingSource(null)}>
                            {t("取消")}
                          </button>
                        </div>
                      </form>
                    )}
                    </div>
                    );
                  };
                  const hasRelevantEvidence = evidence.some(
                    (v) => v.status === "review" || v.assessment?.is_relevant,
                  );
                  const approvedBranches = dashboard.approved_sources.filter(
                    (s) => s.role === "branch" || s.source_id.startsWith("kw_"),
                  );
                  const hasNonCsApprovedBranch = approvedBranches.some(
                    (s) =>
                      !(s.config as Record<string, unknown> | undefined)
                        ?.cold_start,
                  );
                  const hasUnapprovedBranch = sources.some(
                    (s) =>
                      s.health_status === "candidate" &&
                      !(s.config as Record<string, unknown> | undefined)
                        ?.cold_start &&
                      !dashboard.approved_sources.some(
                        (a) => a.source_id === s.source_id,
                      ),
                  );
                  const isColdStartTopic =
                    llmMode === "cold_start" ||
                    (llmMode !== "advisor" &&
                      dashboard.events.length === 0 &&
                      !hasRelevantEvidence &&
                      !hasUnapprovedBranch &&
                      (approvedBranches.length === 0 ||
                        (!hasNonCsApprovedBranch && evidence.length === 0)));
                  const langsDirtyForLlm =
                    llmSummaryOn &&
                    !replay &&
                    searchLanguages.join(",") !== lastLlmLangs;
                  const toggleLanguage = (code: string) => {
                    const nextLangs = searchLanguages.includes(code)
                      ? searchLanguages.filter((l) => l !== code)
                      : [...searchLanguages, code];
                    setSearchLanguages(nextLangs);
                    if (!llmSummaryOn) {
                      void action(async () => {
                        await refreshSources(false, nextLangs);
                      });
                    }
                  };
                  return (
                    <div className="sources-layout">
                      <section className="section-block">
                        <div className="section-title">
                          <h2>{t("追踪词表与信息来源")}</h2>
                          <div className="watchlist-save-actions">
                            <button
                              type="button"
                              disabled={busy || running}
                              onClick={() => showDialog("search")}
                            >
                              {t("搜索引擎")}{searchSettings && ` · ${searchProviderLabel(searchSettings.active_provider)}`}
                            </button>
                            <button
                              disabled={busy || running}
                              onClick={() => showDialog("source")}
                            >
                              <Icon name="plus" size={15} /> {t("添加来源")}
                            </button>
                          </div>
                        </div>
                        <div
                          className={`watchlist-save-bar ${hasUnsavedSources ? "dirty" : ""}`}
                        >
                          <span className="watchlist-save-status">
                            {hasUnsavedSources
                              ? t("有未保存的勾选变更（当前勾选 {0} 项），请点击保存后生效。", approved.length)
                              : isColdStartTopic
                                ? t("已生效 {0} 项。空主题可点击「AI 冷启动拆词」自动生成 ≤3 个初始检索方向与推荐语种。", approved.length)
                                : t("已生效 {0} 项追踪配置。默认使用 0-Token 已有事件事实作为扩展理由。", approved.length)}
                          </span>
                          <div className="watchlist-save-actions">
                            <button
                              type="button"
                              disabled={busy || running}
                              onClick={() =>
                                void action(async () => {
                                  const next = langsDirtyForLlm
                                    ? true
                                    : !llmSummaryOn;
                                  const res = await refreshSources(next);
                                  if (!res) return;
                                  if (next) {
                                    if (res.llm_summary_enabled) {
                                      setToast(
                                        res.llm_mode === "cold_start"
                                          ? t("已生成 AI Cold-Start 初始检索建议，请勾选需要的来源并保存。")
                                          : t("已开启 AI 对比推荐理由（单次轻量总结）。"),
                                      );
                                    } else {
                                      setError(
                                        res.llm_summary_error ||
                                          t("AI 助手未能开启（请检查模型配置），已保持 0-Token 模式。"),
                                      );
                                    }
                                  } else {
                                    setToast(t("已切换回 0-Token 事实依据模式。"));
                                  }
                                })
                              }
                            >
                              {langsDirtyForLlm
                                ? t("按新语种重新生成")
                                : isColdStartTopic
                                  ? llmSummaryOn
                                    ? t("AI 冷启动拆词：ON")
                                    : t("AI 冷启动拆词：OFF")
                                  : llmSummaryOn
                                    ? t("AI 对比理由：ON")
                                    : t("AI 对比理由：OFF (0-Token)")}
                            </button>
                            <button
                              type="button"
                              className="primary"
                              disabled={busy || running || !hasUnsavedSources}
                              onClick={() => void action(saveApprovedSources)}
                            >
                              {t("保存批准来源")} <Icon name="check" size={15} />
                            </button>
                          </div>
                        </div>
                        {!replay ? (
                          <div className="lang-picker-bar">
                            <span className="lang-picker-label">{t("检索语种")}</span>
                            <div className="lang-picker-pills">
                              <button
                                type="button"
                                className={`lang-pill ${searchLanguages.length === 0 ? "active" : ""}`}
                                disabled={busy || running}
                                onClick={() => {
                                  setSearchLanguages([]);
                                  if (!llmSummaryOn) {
                                    void action(async () => {
                                      await refreshSources(false, []);
                                    });
                                  }
                                }}
                              >
                                {t("自动（AI 推荐）")}
                              </button>
                              {SEARCH_LANGUAGE_OPTIONS.map((opt) => (
                                <button
                                  type="button"
                                  key={opt.code}
                                  className={`lang-pill ${searchLanguages.includes(opt.code) ? "active" : ""}`}
                                  disabled={busy || running}
                                  onClick={() => toggleLanguage(opt.code)}
                                >
                                  {t(opt.label)}
                                </button>
                              ))}
                            </div>
                          </div>
                        ) : (
                          <p className="hint replay-lang-hint">
                            {t("离线搜索池按本地语料布尔检索，无需选择网搜语种；空主题下点击「AI 冷启动拆词」可基于目标字面拆出 ≤3 个初始检索支线（零未来泄露）。")}
                          </p>
                        )}
                        {llmSummaryNote && (
                          <div className="llm-summary-box">
                            <div className="llm-summary-head">
                              <strong>
                                {llmMode === "cold_start"
                                  ? t("AI Cold-Start 初始检索方案")
                                  : t("AI 扩展优先级对比建议")}
                              </strong>
                              <span className="llm-meta-badges">
                                {!replay &&
                                  recommendedLanguages.map((lg) => (
                                    <span className="tag lang" key={lg}>
                                      {t("推荐：")}{t(SEARCH_LANGUAGE_MAP.get(lg) ?? lg)}
                                    </span>
                                  ))}
                                {llmUsage?.total_tokens != null && (
                                  <span className="tag quiet">
                                    {t("本次消耗")} {llmUsage.total_tokens} tokens
                                  </span>
                                )}
                              </span>
                            </div>
                            <p>{llmSummaryNote}</p>
                          </div>
                        )}
                        {eventSuggestions.length > 0 && (
                          <div className="ai-event-suggestions">
                            <strong>{t("AI 审计发现以下疑似异常事件（需人工确认）")}</strong>
                            {eventSuggestions.map((sug) => (
                              <div className="ai-event-suggestion-row" key={`${sug.action}-${sug.event_id}`}>
                                <span>
                                  <b>
                                    {sug.action === "merge"
                                      ? t("建议合并：「{0}」→「{1}」", sug.event_title, sug.target_event_title ?? "")
                                      : t("建议移除：「{0}」", sug.event_title)}
                                  </b>
                                  <small>{sug.reason}</small>
                                </span>
                                <div className="action-row">
                                  <button
                                    type="button"
                                    disabled={busy || running}
                                    onClick={() => void applyEventSuggestion(sug)}
                                  >
                                    {sug.action === "merge" ? t("确认合并") : t("移出并记入排除")}
                                  </button>
                                  <button
                                    type="button"
                                    disabled={busy}
                                    onClick={() =>
                                      setEventSuggestions((prev) =>
                                        prev.filter((x) => x.event_id !== sug.event_id),
                                      )
                                    }
                                  >
                                    {t("忽略")}
                                  </button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                        {watchlistSources.length > 0 && (
                          <>
                            <div className="source-group-hd">
                              <span>{t("追踪关键词与支线词表（Watchlist · 盯谁）")}</span>
                              <small>{watchlistSources.length} {t("项")}</small>
                            </div>
                            <div className="source-list">
                              {watchlistSources.map(renderSourceRow)}
                            </div>
                          </>
                        )}
                        {channelSources.length > 0 && (
                          <>
                            <div className="source-group-hd">
                              <span>{t("媒体渠道与语料源（Channels · 去哪抓）")}</span>
                              <small>{channelSources.length} {t("项")}</small>
                            </div>
                            <div className="source-list">
                              {channelSources.map(renderSourceRow)}
                            </div>
                          </>
                        )}
                        <div className="section-actions">
                          <button
                            className="primary"
                            disabled={busy || running || !hasUnsavedSources}
                            onClick={() => void action(saveApprovedSources)}
                          >
                            {t("保存批准来源")} <Icon name="check" size={15} />
                          </button>
                        </div>
                      </section>
                      <aside className="context-panel">
                        <span className="kicker">YOUR PREFERENCES</span>
                        <h3>{t("提醒偏好")}</h3>
                        <label>
                          {t("何时提醒")}
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
                            <option value="standard">{t("重大进展即时提醒")}</option>
                            <option value="digest_only">{t("全部进入更新汇总")}</option>
                          </select>
                        </label>
                        <p className="hint">
                          {t("普通更新进入汇总，低优先级变化仅保留记录。目前提醒展示在工作台内。")}
                        </p>
                        <div className="context-divider" />
                        <h3>{t("想测试离线回放语料？")}</h3>
                        <p className="hint">
                          {t("单独创建回放主题，按时间切片验证多事件建档与关键词演化，避免混入模拟新闻和实时订阅。")}
                        </p>
                        <button
                          className="text-button"
                          onClick={() => showDialog("replay")}
                        >
                          {t("准备回放工作区")} <Icon name="arrow" size={14} />
                        </button>
                      </aside>
                    </div>
                  );
                })()}
                {tab === "report" && <TopicReport dashboard={dashboard} notices={notices} onEvent={(id) => void openEvent(id)} />}
                {tab === "activity" && (
                  <div className="activity-layout">
                    <section className="run-list">
                      <h2>{t("检查历史")}</h2>
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
                          <dl className="run-metrics">
                            {([
                              ["读取", run.run.counts.fetched], ["已记录更新", run.run.counts.committed],
                              ["跳过", run.run.counts.skipped], ["待审核", run.run.counts.review],
                            ] as const).map(([label, count]) => <div key={label}><dt>{t(label)}</dt><dd>{count ?? 0}</dd></div>)}
                          </dl>
                          <SourceHealth steps={run.steps}
                            sources={run.run.snapshot.sources ?? dashboard.approved_sources}
                            onSources={() => openTab("sources", true)} />
                          <h3 className="run-stage-title">{t("执行步骤")} <span>{run.steps.length}</span></h3>
                          {run.run.error != null && (run.steps.some((step) => step.step_name === "fetch_source" && step.status === "failed") ? (
                            <details className="run-error-details">
                              <summary>{t("技术错误详情")}</summary>
                              <pre>{pretty(run.run.error)}</pre>
                            </details>
                          ) : <pre className="error">{pretty(run.run.error)}</pre>)}
                          {unfinished.length > 0 && !active(run.run.status) && (
                            <>
                              <div className="section-title">
                                <h3>{t("失败或未处理的证据")}</h3>
                                <button
                                  disabled={running || busy}
                                  onClick={() =>
                                    void action(() =>
                                      resumeRun(run.run.run_id),
                                    )
                                  }
                                >
                                  {t("续跑未完成证据")}
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
                                      {item.attempts} {t("次尝试")}
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
                                    <span className="trace-stage"><b>{String(s.sequence).padStart(2, "0")}</b>{stepLabel(s.step_name)}</span>
                                    <span className="meta">
                                      {status(s.status)} · {s.attempt} {t("次")}
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
                                        {t("查看处理的新闻")}{" "}
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
                            <summary>{t("完整运行记录")}</summary>
                            <pre>{pretty(run.run)}</pre>
                          </details>
                        </>
                      ) : (
                        <Empty
                          title={t("还没有运行记录")}
                          detail={t("每次检查都会留下有顺序的步骤、处理结果和错误。")}
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
            <span>COMP7608 Large Language Models</span>
          </footer>
        </main>
      </div>
    </>
  );

  const removingEvent = dialog === "delete-event";
  const removalTitle = removingEvent && deleteEventTarget
    ? eventTitle(deleteEventTarget.title)
    : dialog === "delete" && deleteTarget ? topicTitle(deleteTarget.title) : null;

  return (
    <TranslationContext.Provider value={tr}><div className="workspace" style={{ "--sidebar-width": `${sidebar.width}px` } as React.CSSProperties}>
      {!selected ? (
        <ProjectHome
          topics={orderedTopics}
          loading={loading}
          busy={busy}
          feedback={feedback}
          preferences={<>{preferences}{translationToggle}</>}
          onHome={() => navigateTopic("")}
          onTopic={navigateTopic}
          onNew={openNewTopic}
          onSettings={() => showDialog("settings")}
        />
      ) : (
        workspaceView
      )}
      {dialog && (
        <Modal
          title={
            {
              new: t("新建追踪主题"),
              goal: t("编辑追踪目标"),
              source: t("添加信息来源"),
              manual: t("提交一条新闻"),
              settings: t("配置真实模型"),
              search: t("配置联网搜索引擎"),
              replay: t("准备新闻回放"),
              delete: t("删除主题"),
              "delete-event": t("删除事件"),
            }[dialog]
          }
          onClose={() => setDialog(null)}
          compact={Boolean(removalTitle)}
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
                      tags: newTags,
                    }),
                  );
                  setTopics(await loadWorkspaceTopics());
                  selectTopic(t.topic_id);
                  setDialog(null);
                  setTab("sources");
                  setNewTitle("");
                  setNewDescription("");
                  setNewTags([]);
                });
              }}
            >
              <label>
                {t("主题名称")}
                <input
                  autoFocus
                  required
                  maxLength={200}
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder={t("例如：某公司的收购进展")}
                />
              </label>
              <label>
                {t("你想持续了解什么？")}
                <textarea
                  required
                  maxLength={4000}
                  value={newDescription}
                  onChange={(e) => setNewDescription(e.target.value)}
                  placeholder={t("说明关注的事项、重要变化和需要区分的情况。")}
                />
              </label>
              <TopicTagPicker value={newTags} options={tagOptions(topics)} onChange={setNewTags} />
              <button className="primary" disabled={busy}>
                {t("创建主题")} <Icon name="arrow" size={16} />
              </button>
            </form>
          )}
          {dialog === "goal" && dashboard && (
            <GoalEditor
              topic={dashboard.topic}
              availableTags={tagOptions(topics)}
              disabled={busy || running}
              action={action}
              onSaved={async () => {
                await refresh();
                setTopics(await loadWorkspaceTopics());
                setDialog(null);
                setToast(t("追踪目标已更新。"));
              }}
            />
          )}
          {dialog === "source" && (
            <RegisterSource
              disabled={busy || running}
              action={action}
              onSaved={async () => {
                await refreshSources(false);
                setDialog(null);
                setTab("sources");
                setToast(t("来源已添加，请勾选并保存批准。"));
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
          {dialog === "search" && (
            <SearchSettingsPanel
              value={searchSettings}
              disabled={busy || running}
              action={action}
              onSaved={async (next) => {
                setSearchSettings(next);
                if (selected && !llmSummaryOn) {
                  await refreshSources(false);
                }
              }}
            />
          )}
          {dialog === "replay" && (
            <ReplaySetup
              corpora={corpora}
              disabled={busy}
              action={action}
              onPrepared={async (topic, c) => {
                setTopics(await loadWorkspaceTopics());
                selectTopic(topic.topic_id);
                setProvider("qwen");
                setAsOf(localInput(c.cutoffs[0]));
                setDialog(null);
                setToast(t("回放工作区已准备好。配置模型后，从阶段 1 开始。"));
              }}
            />
          )}
          {removalTitle && (
            <div className="topic-removal form-stack">
              <h3>{tr(removalTitle)}</h3>
              <p>{t(removingEvent
                ? "将永久删除此事件、进展时间线与提醒。新闻原文和运行记录会保留。"
                : "将永久删除此主题及其事件、运行记录和审核记录。其他主题共用的新闻与来源会保留。")}</p>
              {removingEvent && (
                <>
                  <div className="action-row">
                    <label className="check-row">
                      <input
                        type="radio"
                        name="quick-dismiss-disposition"
                        checked={deleteEventDisposition === "irrelevant"}
                        onChange={() => setDeleteEventDisposition("irrelevant")}
                      />
                      {t("不相关 / 误报")}
                    </label>
                    <label className="check-row">
                      <input
                        type="radio"
                        name="quick-dismiss-disposition"
                        checked={deleteEventDisposition === "unwanted"}
                        onChange={() => setDeleteEventDisposition("unwanted")}
                      />
                      {t("不想继续追踪")}
                    </label>
                  </div>
                  <label>
                    {t("原因说明（将作为长期排除规则反馈给后续检查）")}
                    <input
                      maxLength={160}
                      value={deleteEventReason}
                      onChange={(e) => setDeleteEventReason(e.target.value)}
                      placeholder={t("例如：排除无畏契约分部，仅追踪英雄联盟 LPL 分部")}
                    />
                  </label>
                  <label className="check-row">
                    <input
                      type="checkbox"
                      checked={deleteEventAddExclusion}
                      onChange={(e) => setDeleteEventAddExclusion(e.target.checked)}
                    />
                    {t("沉淀为该主题的长期排除规则（可在编辑目标中随时撤回）；原新闻会保留为已排除，下次检查不重复建档")}
                  </label>
                </>
              )}
              <div className="topic-removal-actions">
                <button type="button" autoFocus disabled={busy} onClick={() => setDialog(null)}>{t("取消")}</button>
                <button type="button" className="danger-button" disabled={busy}
                  onClick={removingEvent ? confirmDeleteEvent : confirmDelete}>{t("确认删除")}</button>
              </div>
            </div>
          )}
        </Modal>
      )}
      {event && (
        <Modal title={t("事件档案")} onClose={() => setEvent(null)} wide sheet>
          <EventView
            value={event}
            onEvidence={setEvidenceId}
            otherEvents={(dashboard?.events ?? []).filter(
              (item) => item.event_id !== event.event.event_id,
            )}
            disabled={busy || running}
            onPatchTitle={async (nextTitle, locked) => {
              await action(async () => {
                const updated = await api<EventDetail>(
                  `/events/${event.event.event_id}`,
                  json("PATCH", { title: nextTitle, title_locked: locked }),
                );
                setEvent(updated);
                await refresh();
                setToast(t("事件标题已更新。"));
              });
            }}
            onDismiss={async (payload) => {
              await action(async () => {
                await api(
                  `/events/${event.event.event_id}/dismiss`,
                  json("POST", payload),
                );
                setEventSuggestions((prev) =>
                  prev.filter((x) => x.event_id !== event.event.event_id),
                );
                setEvent(null);
                await refresh();
                setTopics(await loadWorkspaceTopics());
                setToast(
                  payload.add_exclusion
                    ? t("已移除事件并加入主题排除规则，原新闻不会重复建档。")
                    : t("已移除事件，原新闻已标为已排除。"),
                );
              });
            }}
            onMerge={async (payload) => {
              await action(async () => {
                const merged = await api<EventDetail>(
                  `/events/${event.event.event_id}/merge`,
                  json("POST", payload),
                );
                setEventSuggestions((prev) =>
                  prev.filter((x) => x.event_id !== event.event.event_id),
                );
                setEvent(merged);
                await refresh();
                setToast(t("已合并事件，事实与时间线已整合。"));
              });
            }}
          />
        </Modal>
      )}
      {selectedEvidence && (
        <Modal title={t("新闻与判断依据")} onClose={() => setEvidenceId("")}>
          <EvidenceView value={selectedEvidence} />
        </Modal>
      )}
    </div></TranslationContext.Provider>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
