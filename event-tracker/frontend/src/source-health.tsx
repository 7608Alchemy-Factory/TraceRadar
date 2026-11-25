import { t } from "./i18n";
import type { Source, Step } from "./types";

export function searchProviderLabel(provider: string | undefined) {
  switch (provider) {
    case "bocha": return t("博查 API");
    case "tavily": return "Tavily";
    case "free_bing_rss": return "Bing News";
    case "free_google_rss": return "Google News";
    case "free_bing_and_google_rss": return "Bing News + Google News";
    case "free": return t("免密钥模式");
    default: return provider ?? t("联网搜索");
  }
}

export function SourceHealth({ steps, sources, onSources }: {
  steps: Step[];
  sources: Source[];
  onSources: () => void;
}) {
  const updates = steps.flatMap((step) => {
    if (step.step_name !== "fetch_source") return [];
    const id = step.input_refs[0];
    const name = sources.find((source) => source.source_id === id)?.name ?? id;
    const stats = (step.decision as { stats?: { fallback_from?: string; provider_used?: string } } | null)?.stats;
    if (stats?.fallback_from === "google_news_rss") {
      return [{ id, name, recovered: true, detail: t("Google News RSS 无法连接，已改用 {0}。", searchProviderLabel(stats.provider_used)) }];
    }
    if (step.status !== "failed") return [];
    const error = step.error as { message?: string; cause_code?: string } | null;
    const reasons: Record<string, string> = {
      RSS_FALLBACK_UNAVAILABLE: "Google News RSS 和备用搜索均无法连接。",
      SOURCE_UNAVAILABLE: "当前无法连接到这个来源。",
      SEARCH_AUTH_ERROR: "请检查搜索引擎的 API 密钥。",
    };
    const detail = t(error?.message?.includes("TimeoutException") ? "RSS 连接超时。"
      : reasons[error?.cause_code ?? ""] ?? "读取失败，详情见对应执行步骤。");
    return [{ id, name, recovered: false, detail }];
  });
  if (!updates.length) return null;
  return (
    <section className="source-health" aria-label={t("来源读取情况")}>
      <h3>{t("来源读取情况")}</h3>
      <ul>
        {updates.map(({id, name, recovered, detail}) => (
          <li key={id}>
            <span className={`tag ${recovered ? "green" : "amber"}`}>{t(recovered ? "已使用备用来源" : "无法读取")}</span>
            <div><strong>{name}</strong><p>{detail}</p></div>
          </li>
        ))}
      </ul>
      {updates.some((item) => !item.recovered) && (
        <button type="button" onClick={onSources}>{t("检查信息源")}</button>
      )}
    </section>
  );
}
