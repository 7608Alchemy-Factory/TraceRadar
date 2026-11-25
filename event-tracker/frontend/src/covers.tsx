import { categories, isCategory } from "./topic-tags";
import type { Topic } from "./types";

const coverDetails: Record<keyof typeof categories | "tools", string> = {
  tech:        "+--: .\n  +--o\n    .",
  education:   "  -+\n .---.\n    :",
  travel:      " .   .\no--+--o\n     .",
  science:     "  . *\n . : .\n   +",
  food:        "   .\n  : .\n .   .",
  law:         "   .\n--+-- .\n  |",
  sports:      "  / .\n // -\n/  .",
  deals:       " . :\n   % .\n     .",
  politics:    "  .\n |:| .\n  --",
  community:   "  /\\ .\n /  \\  .\n     :",
  international:" . /\n  / .\n +    .",
  business:    "   + .\n  ||\n : |",
  environment: "  .\n .~: .\n  . ~",
  culture:     "  . *\n :    .\n   .",
  health:      "     .\n--/\\--\n   .",
  tools:       "  + .\n .---\n   :",
};

export function CoverImage({ topic, className = "" }: { topic: Topic; className?: string }) {
  const primary = topic.tags?.[0]?.toLowerCase() ?? "";
  const cover = isCategory(primary) ? primary : "tools";
  return (
    <div className={`cover-art ${className}`} data-topic-cover={topic.topic_id}
      data-cover={cover} aria-hidden="true">
      <div className="cover-illustration">
        <img className="cover-print" src={`/covers/categories/${cover}.webp`} alt=""
          width={360} height={180} loading={className === "frame-cover" ? "lazy" : undefined} />
        <span className="cover-ascii">{coverDetails[cover]}</span>
      </div>
    </div>
  );
}
