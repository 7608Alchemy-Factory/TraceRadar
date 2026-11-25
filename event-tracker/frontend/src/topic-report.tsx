import { date, status, title, longDate, headline } from "./display";
import { t } from "./i18n";
import { useTranslation } from "./reference-translation";
import type { Dashboard, Notification } from "./types";

export const priorities = ["breaking", "routine", "low"] as const;
export type Priority = typeof priorities[number];
export const priorityLabel = (value: Priority) => t({
  breaking: "极重要更新", routine: "重要更新", low: "次要更新",
}[value]);

export function TopicReport({ dashboard, notices, onEvent }: {
  dashboard: Dashboard; notices: Notification[]; onEvent: (id: string) => void;
}) {
  const tr = useTranslation();
  const events = [...dashboard.events].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  const total = priorities.map((priority) => notices.filter((notice) => notice.priority === priority).length);
  return (
    <section className="topic-report">
      <header><h2>{t("追踪报告")}</h2><p>{t("按已入档的更新查看事件变化、当前结论和待核实证据。")}</p></header>
      <div className="report-metrics">
        {priorities.map((priority, index) => (
          <div data-impact={priority} key={priority}><span>{priorityLabel(priority)}</span><strong>{total[index]}</strong></div>
        ))}
        <div data-impact="review"><span>{t("待审核")}</span><strong>{dashboard.reviews.length}</strong></div>
      </div>
      <h3>{t("事件结论")}</h3>
      {!events.length && <p>{t("事件会在这里逐步形成")}</p>}
      {events.map((event) => {
        const updates = notices.filter((item) => item.event_id === event.event_id);
        const counts = priorities.map((priority) => updates.filter((item) => item.priority === priority).length);
        const latest = event.state.timeline.at(-1);
        return (
          <article key={event.event_id} className="report-event">
            <div className="report-event-heading">
              <h4>{tr(title(event.title))}</h4><time dateTime={event.updated_at}>{date(event.updated_at)}</time>
            </div>
            <p>{tr(status(event.state.current_status))}</p>
            {latest && <p className="report-latest"><time dateTime={latest.occurred_at}>{longDate(latest.occurred_at)}</time> {tr(headline(latest.description))}</p>}
            {updates.length > 0 && (
              <div className="report-distribution">
                <div className="report-bar" aria-hidden="true">
                  {counts.map((count, index) => count > 0 && (
                    <span key={priorities[index]} data-impact={priorities[index]} style={{ flexGrow: count }} />
                  ))}
                </div>
                <ul>{counts.map((count, index) => <li key={priorities[index]}>{priorityLabel(priorities[index])} <b>{count}</b></li>)}</ul>
              </div>
            )}
            <button type="button" onClick={() => onEvent(event.event_id)}>{t("事件档案")}</button>
          </article>
        );
      })}
      {!!notices.length && <section className="report-recent">
        <h3>{t("最近入档更新")}</h3>
        <ul>{notices.slice(0, 6).map((notice) => {
          const event = events.find((item) => item.event_id === notice.event_id);
          return event && <li key={notice.notification_id}>
            <time dateTime={notice.created_at}>{longDate(notice.created_at)}</time>
            <button onClick={() => onEvent(event.event_id)}>{tr(title(event.title))}</button>
            <span data-impact={notice.priority}>{priorityLabel(notice.priority as Priority)}</span>
          </li>;
        })}</ul>
      </section>}
    </section>
  );
}
