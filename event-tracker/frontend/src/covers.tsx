import { isCategory } from "./topic-tags";
import type { Topic } from "./types";

export function CoverImage({ topic, className = "" }: { topic: Topic; className?: string }) {
  const primary = topic.tags?.[0]?.toLowerCase() ?? "";
  const cover = isCategory(primary) ? primary : "tools";
  return (
    <div className={`cover-art ${className}`} data-topic-cover={topic.topic_id}
      data-cover={cover} aria-hidden="true">
      <img className="cover-print" src={`/covers/categories/${cover}.webp`} alt=""
        width={360} height={180} loading={className === "frame-cover" ? "lazy" : undefined} />
    </div>
  );
}
