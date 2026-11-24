import type { CSSProperties } from "react";
import { coverGlyphs } from "./cover-glyphs";
import type { Topic } from "./types";

type Cover = keyof typeof coverGlyphs;
const positions: Record<Cover, string> = {
  radar: "0% 0%", sports: "100% 0%", food: "0% 50%",
  technology: "100% 50%", transport: "0% 100%", archive: "100% 100%",
};
const categoryCovers = new Map<string, Cover>([
  ["tech", "technology"], ["travel", "transport"], ["food", "food"], ["sports", "sports"],
]);

function subject(title: string): Cover {
  if (/examplesoft|security|資安|安全|漏洞|\bcve\b/i.test(title)) return "radar";
  if (/\b(nba|lbj|lebron|basketball)\b|籃球|篮球|轉會|转会|球隊|球队/i.test(title)) return "sports";
  if (/mcdonald|burger|restaurant|food|麥當勞|麦当劳|美食|餐飲|餐饮/i.test(title)) return "food";
  if (/\b(ai|llm|chip|semiconductor)\b|人工智能|生成式|芯片|晶片/i.test(title)) return "technology";
  if (/transport|transit|tram|railway|交通|電車|电车|地鐵|地铁/i.test(title)) return "transport";
  return "archive";
}

export function CoverImage({ topic, className = "" }: { topic?: Topic; className?: string }) {
  const category = topic?.tags?.map((tag) => categoryCovers.get(tag)).find((value) => value !== undefined);
  const cover = category ?? (topic ? subject(topic.title) : "radar");
  return (
    <div className={`cover-art ${className}`} data-cover={cover} data-topic-cover={topic?.topic_id}
      style={{ "--cover-position": positions[cover] } as CSSProperties} aria-hidden="true">
      <div className="cover-print">
        <span className="cover-glyphs">
          {coverGlyphs[cover].map(([x, y, glyph]) => (
            <span key={`${x}-${y}`} style={{ left: `${x}%`, top: `${y}%` }}>{glyph}</span>
          ))}
        </span>
      </div>
    </div>
  );
}
