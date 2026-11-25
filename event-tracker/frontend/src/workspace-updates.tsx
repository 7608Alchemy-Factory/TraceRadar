import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { formatScheduledTime } from "./clock";
import { title } from "./display";
import { t } from "./i18n";
import { useTranslation } from "./reference-translation";
import type { Inbox, InboxItem, Schedule } from "./types";
import { Icon } from "./ui";

type Updates = { inbox: Inbox; schedules: Schedule[]; topic_inbox: Inbox | null };

export function useWorkspaceUpdates(topicId: string) {
  const [updates, setUpdates] = useState<Updates & { topicId: string }>();
  const [incoming, setIncoming] = useState<InboxItem | null>(null);
  const latest = useRef<string | undefined>(undefined);
  const request = useRef(0);
  const reload = useCallback(async (signal?: AbortSignal) => {
    const version = ++request.current;
    const query = topicId ? `?topic_id=${encodeURIComponent(topicId)}` : "";
    const data = await api<Updates>(`/workspace-updates${query}`, { signal });
    if (signal?.aborted || request.current !== version) return;
    setUpdates({ ...data, topicId });
    const id = data.inbox.latest_immediate_id;
    const item = data.inbox.items.find((notice) => notice.notification_id === id);
    if (item && latest.current !== undefined && item.created_at > latest.current && !item.read_at) {
      setIncoming(item);
    }
    if (latest.current === undefined || (item && item.created_at > latest.current)) latest.current = item?.created_at ?? "";
  }, [topicId]);

  useEffect(() => {
    const abort = new AbortController();
    let timer: number;
    async function poll() {
      try {
        await reload(abort.signal);
      } catch {
        // Keep the last inbox and schedule state while the local service restarts.
      }
      if (!abort.signal.aborted) timer = window.setTimeout(poll, 5000);
    }
    void poll();
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [reload]);
  return { updates, incoming, dismissIncoming: () => setIncoming(null), reload };
}

export function NoticeRows({ items, timeZone, onOpen, showTopic = false }: {
  items: InboxItem[]; timeZone: string; onOpen: (item: InboxItem) => void; showTopic?: boolean;
}) {
  const tr = useTranslation();
  return <div className="notice-list">{items.map((item) => <button type="button" className="notice-card"
    data-action={item.action} data-unread={!item.read_at} key={item.notification_id} onClick={() => onOpen(item)}>
    <span className="notice-card-copy">
      <span className="notice-kind"><Icon name={item.action === "immediate" ? "bell" : "evidence"} size={14} />
        {t(item.action === "immediate" ? "即时提醒" : "更新汇总")}
        {!item.read_at && <span className="notice-unread">{t("未读")}</span>}
      </span>
      <strong>{tr(title(item.event_title))}</strong>
      {showTopic && <small>{item.topic_title}</small>}
      {item.summary && <small className="notice-summary">{tr(item.summary)}</small>}
    </span>
    <span className="notice-card-end">
      <time dateTime={item.created_at}>{formatScheduledTime(item.created_at, timeZone)}</time><Icon name="arrow" size={15} />
    </span>
  </button>)}</div>;
}

export function InboxPanel({ inbox, timeZone, busy, onOpen, onReadAll, showTopic }: {
  inbox?: Inbox | null; timeZone: string; busy: boolean; onOpen: (item: InboxItem) => void;
  onReadAll: () => void; showTopic: boolean;
}) {
  const [filter, setFilter] = useState<"all" | "immediate" | "digest">("all");
  const items = inbox?.items.filter((item) => filter === "all" || item.action === filter) ?? [];
  return <>
    <div className="inbox-controls">
      <div className="inbox-filters" role="group" aria-label={t("提醒类型")}>
        {([
          ["all", "全部提醒"], ["immediate", "即时提醒"], ["digest", "更新汇总"],
        ] as const).map(([id, label]) => <button key={id} type="button" aria-pressed={filter === id} onClick={() => setFilter(id)}>{t(label)}</button>)}
      </div>
      {showTopic && <button type="button" className="text-button" disabled={busy || !inbox?.unread} onClick={onReadAll}>{t("全部标为已读")}</button>}
    </div>
    {items.length ? <NoticeRows items={items} onOpen={onOpen} timeZone={timeZone} showTopic={showTopic} />
      : <p className="blank-line">{t("此处暂无提醒。")}</p>}
  </>;
}
