import { useState } from "react";
import { t } from "./i18n";
import type { Topic } from "./types";

export const categories = {
  tech: ["科技", "生成式 AI 政策", "芯片新品发布"],
  education: ["教育", "学校招生与课程调整", "考试与奖学金政策"],
  travel: ["出行", "城市轨道交通规划", "航班与铁路服务变动"],
  science: ["科学", "科研发现与后续验证", "航天任务进展"],
  food: ["饮食", "餐饮新品与联名", "食品安全通报"],
  law: ["法律", "立法与司法裁决", "监管执法进展"],
  sports: ["体育", "NBA 交易与续约", "大型赛事赛程变化"],
  deals: ["优惠", "香港麦当劳优惠", "电商促销与会员优惠"],
  politics: ["时政", "公共政策与咨询进展", "选举与议会动态"],
  community: ["民生", "社区公共服务变动", "住房与社区规划"],
  international: ["国际", "国际合作与峰会", "跨境协定进展"],
  business: ["财经", "公司收购进展", "利率和通胀数据"],
  environment: ["环境", "极端天气预警", "城市减排计划"],
  culture: ["文化", "展览和演出排期", "电影节与新片发行"],
  health: ["健康", "公共卫生通报", "医疗服务政策"],
} as const;

export const MAX_TOPIC_TAGS = 3;

export function isCategory(value: string): value is keyof typeof categories {
  return Object.hasOwn(categories, value);
}

export function tagLabel(tag: string) {
  return isCategory(tag) ? t(categories[tag][0]) : tag;
}

export function tagExamples(tag: string) {
  return isCategory(tag) ? categories[tag].slice(1).map((example) => t(example)) : [];
}

export function tagOptions(topics: Topic[]) {
  const tags = new Map(Object.keys(categories).map((tag) => [tag, tag]));
  for (const topic of topics) {
    for (const tag of topic.tags ?? []) {
      if (!tags.has(tag.toLocaleLowerCase())) tags.set(tag.toLocaleLowerCase(), tag);
    }
  }
  return [...tags.values()];
}

export function TopicTagPicker({ value, options, onChange }: {
  value: string[]; options: string[]; onChange: (value: string[]) => void;
}) {
  const [input, setInput] = useState("");
  const normalized = input.trim();
  const sameTag = (first: string, second: string) => first.toLocaleLowerCase() === second.toLocaleLowerCase();
  const choices = [...options, ...value.filter((tag) => !options.some((candidate) => sameTag(tag, candidate)))];
  const full = value.length >= MAX_TOPIC_TAGS;
  function addCustom() {
    if (!normalized || full) return;
    const tag = choices.find((candidate) => sameTag(normalized, candidate) || sameTag(normalized, tagLabel(candidate))) ?? normalized;
    if (!value.some((candidate) => sameTag(tag, candidate))) onChange([...value, tag]);
    setInput("");
  }
  return (
    <fieldset className="topic-tag-picker">
      <legend>{t("主题类型")}<small>{t("（最多勾选3项）")}</small></legend>
      <div className="topic-tag-options">
        {choices.map((tag) => {
          const rank = value.findIndex((candidate) => sameTag(tag, candidate)) + 1;
          const selected = rank > 0;
          return <button type="button" className="topic-tag" key={tag} aria-pressed={selected}
            aria-label={selected ? t("{0}，优先级 {1}", tagLabel(tag), rank) : undefined}
            disabled={!selected && full}
            onClick={() => onChange(selected ? value.filter((candidate) => !sameTag(tag, candidate)) : [...value, tag])}>
            {tagLabel(tag)}
            {selected && <span className="tag-rank" aria-hidden="true">{rank}</span>}
          </button>;
        })}
      </div>
      <div className="custom-tag">
        <input aria-label={t("自定义标签")} placeholder={t("自定义标签")} value={input} maxLength={36}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              addCustom();
            }
          }} />
        <button type="button" disabled={!normalized || full} onClick={addCustom}>{t("添加标签")}</button>
      </div>
      <p className="tag-help">
        {t(value.length > MAX_TOPIC_TAGS ? "此主题的旧标签超过 3 项。请取消多余标签后再保存。" : "优先级 1 的标签决定封面。")}
      </p>
    </fieldset>
  );
}
