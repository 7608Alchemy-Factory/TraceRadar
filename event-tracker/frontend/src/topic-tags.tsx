import { useState } from "react";
import { t } from "./i18n";
import type { Topic } from "./types";
import { categories, MAX_TOPIC_TAGS } from "./topic-categories";
export { categories, MAX_TOPIC_TAGS };

export function isCategory(value: string): value is keyof typeof categories {
  return Object.hasOwn(categories, value);
}

export function tagLabel(tag: string) {
  return isCategory(tag) ? t(categories[tag]) : tag;
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
