import { useState } from "react";
import { t } from "./i18n";
import type { Topic } from "./types";

export const categories = {
  tech: ["科技", "生成式 AI 政策", "芯片新品发布"],
  travel: ["出行", "城市轨道交通规划", "航班与铁路服务变动"],
  food: ["饮食", "餐饮新品与联名", "食品安全通报"],
  sports: ["体育", "NBA 交易与续约", "大型赛事赛程变化"],
  deals: ["优惠", "香港麦当劳优惠", "电商促销与会员优惠"],
  politics: ["时政", "公共政策与咨询进展", "选举与议会动态"],
  business: ["财经", "公司收购进展", "利率和通胀数据"],
  environment: ["环境", "极端天气预警", "城市减排计划"],
  culture: ["文化", "展览和演出排期", "电影节与新片发行"],
  health: ["健康", "公共卫生通报", "医疗服务政策"],
} as const;

function isCategory(value: string): value is keyof typeof categories {
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
  function addCustom() {
    if (!normalized || value.length >= 12) return;
    const tag = choices.find((candidate) => sameTag(normalized, candidate) || sameTag(normalized, tagLabel(candidate))) ?? normalized;
    if (!value.some((candidate) => sameTag(tag, candidate))) onChange([...value, tag]);
    setInput("");
  }
  return (
    <fieldset className="topic-tag-picker">
      <legend>{t("主题类型")}</legend>
      <div className="topic-tag-options">
        {choices.map((tag) => {
          const selected = value.some((candidate) => sameTag(tag, candidate));
          return <button type="button" className="topic-tag" key={tag} aria-pressed={selected}
            disabled={!selected && value.length >= 12}
            onClick={() => onChange(selected ? value.filter((candidate) => !sameTag(tag, candidate)) : [...value, tag])}>{tagLabel(tag)}</button>;
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
        <button type="button" disabled={!normalized || value.length >= 12} onClick={addCustom}>{t("添加标签")}</button>
      </div>
    </fieldset>
  );
}
