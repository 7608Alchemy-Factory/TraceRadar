import { locale, t } from "./i18n";
export const pretty = (value: unknown) => JSON.stringify(value, null, 2);
export const active = (status?: string) =>
  status === "pending" || status === "running";
export const date = (value: string | null | undefined, withTime = true) =>
  value
    ? new Intl.DateTimeFormat(locale(), {
        timeZone: "Asia/Hong_Kong",
        month: "2-digit",
        day: "2-digit",
        ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
      }).format(new Date(value))
    : "—";
export const localInput = (iso: string) => {
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};
export function longDate(value: string) {
  const time = Date.parse(value);
  return Number.isNaN(time) ? value : new Intl.DateTimeFormat(locale(), {
    timeZone: "Asia/Hong_Kong", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(time);
}
export function title(value: string) {
  const demo = /^ExampleSoft vulnerability ([AB])$/.exec(value);
  return demo ? t("ExampleSoft 漏洞 {0}", demo[1]) : value;
}
export function topicTitle(value: string) {
  return /^(ExampleSoft · 三步演示|LeBron James 转会追踪|NBA 多事件追踪)$/.test(value) ? t(value) : value;
}
export function topicDescription(value: string) {
  if (value === "Track ExampleSoft vulnerabilities, affected versions and official patches. Keep conflicting reports for review.") {
    return t("追踪 ExampleSoft 的漏洞、受影响的版本以及官方补丁。保留相互矛盾的报道供审核。");
  }
  return value.startsWith("用三批模拟新闻追踪 ExampleSoft") ? t(value) : value;
}
export const status = (value: string) =>
  ({
    patched: t("已修复"),
    disclosed: t("已披露"),
    completed: t("已完成"),
    completed_with_errors: t("部分失败"),
    running: t("运行中"),
    processing: t("处理中"),
    skipped: t("跳过"),
    pending: t("等待中"),
    failed: t("失败"),
    failed_retryable: t("服务故障，可重试"),
    not_attempted: t("未处理"),
    review: t("待审核"),
    draft: t("待配置"),
    active: t("追踪中"),
    cancelled: t("已取消"),
  })[value] ?? value;
export const provider = (value: string) =>
  ({ mock: t("规则演示"), qwen: t("模型分析"), human: t("人工审核") })[value] ?? value;
export const actionLabel = (value: string) =>
  ({ immediate: t("即时提醒"), digest: t("更新汇总"), record_only: t("仅记录") })[
    value
  ] ?? value;
export const stepLabel = (value: string) =>
  ({
    fetch_source: t("读取来源"),
    normalize_and_deduplicate: t("清理与去重"),
    prefilter_relevance: t("相关性预筛选"),
    assess_evidence: t("判断事件与变化"),
    validate_and_commit: t("校验并保存"),
    human_review_commit: t("人工审核与状态修正"),
  })[value] ?? value;
export function text(value: string) {
  const known: Record<string, string> = {
    "SIMULATED: ExampleSoft vulnerability A disclosed. A remote attacker can access private records.":
      t("漏洞 A 被披露：远程攻击者可能访问私密记录。"),
    "SIMULATED: ExampleSoft vulnerability A affects versions 1.0 and 1.1, confirmed by the vendor.":
      t("厂商确认：1.0 与 1.1 版本受漏洞 A 影响。"),
    "SIMULATED: ExampleSoft vulnerability A patch released in version 1.2.":
      t("1.2 版本补丁已发布，漏洞 A 的修复状态更新。"),
    "SIMULATED: ExampleSoft vulnerability B disclosed. A separate input-validation flaw causes service interruption.":
      t("独立漏洞 B 被披露：输入校验缺陷可能导致服务中断。"),
    "SIMULATED: A report about ExampleSoft vulnerability A contradicts the vendor: it claims version 1.1 is not affected.":
      t("另一篇报道声称 1.1 版本不受影响，与厂商报告冲突。"),
    "SIMULATED: ExampleSoft may have another security issue; no identifier or reliable detail is available.":
      t("可能存在另一安全问题，但缺少标识及可靠细节。"),
    "SIMULATED: ExampleSoft vulnerability A editorial clarification.":
      t("漏洞 A 的补充说明（模拟测试信息）。"),
  };
  return known[value] ?? value;
}
export function safeUrl(value: string) {
  try {
    const u = new URL(value);
    return ["https:", "http:"].includes(u.protocol) ? u.href : undefined;
  } catch {
    return undefined;
  }
}
export function headline(value: string) {
  const known: Record<string, string> = {
    "Simulated: vulnerability A disclosed": t("漏洞 A 首次披露"),
    "Simulated: affected versions confirmed": t("确认受影响的版本"),
    "Simulated: mirrored version report": t("同一版本报道的转载"),
    "Simulated: independent vulnerability B": t("发现独立漏洞 B"),
    "Simulated: patch released for A": t("漏洞 A 的补丁发布"),
    "Simulated: conflicting affected-version report": t("受影响版本出现矛盾报道"),
    "Simulated: unclear clue": t("缺乏细节的新线索"),
    "Simulated: sports update": t("无关的体育新闻"),
  };
  return known[value] ?? value;
}
