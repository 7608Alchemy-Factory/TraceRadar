import { useRef, useState, type DragEvent } from "react";
import { topicTitle } from "./display";
import { t } from "./i18n";
import { useTranslation } from "./reference-translation";
import type { Topic } from "./types";
import { Icon } from "./ui";

type DropPosition = "before" | "after";

function dropPosition(event: DragEvent<HTMLDivElement>): DropPosition {
  const rect = event.currentTarget.getBoundingClientRect();
  if (window.matchMedia("(max-width: 700px)").matches) {
    return event.clientX < rect.left + rect.width / 2 ? "before" : "after";
  }
  return event.clientY < rect.top + rect.height / 2 ? "before" : "after";
}

export function SidebarTopics({ topics, selected, pinned, busy, onSelect, onPin, onMove }: {
  topics: Topic[];
  selected: string;
  pinned: ReadonlySet<string>;
  busy: boolean;
  onSelect: (id: string) => void;
  onPin: (id: string) => void;
  onMove: (id: string, target: string, position: DropPosition) => void;
}) {
  const tr = useTranslation();
  const dragged = useRef("");
  const [drop, setDrop] = useState<{ id: string; position: DropPosition }>();

  function endDrag() {
    dragged.current = "";
    setDrop(undefined);
  }

  return (
    <div className="topic-list">
      {topics.map((topic, index) => {
        const id = topic.topic_id;
        const isPinned = pinned.has(id);
        const label = tr(topicTitle(topic.title));
        const pinLabel = t(isPinned ? "取消置顶" : "置顶");
        return (
          <div className="topic-list-entry" key={id} draggable={!busy}
            data-pinned={isPinned} data-drop={drop?.id === id ? drop.position : undefined}
            onDragStart={(event) => {
              dragged.current = id;
              event.dataTransfer.effectAllowed = "move";
              event.dataTransfer.setData("text/plain", id);
            }}
            onDragOver={(event) => {
              if (busy || !dragged.current || dragged.current === id || pinned.has(dragged.current) !== isPinned) {
                setDrop(undefined);
                return;
              }
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              const position = dropPosition(event);
              setDrop((previous) => previous?.id === id && previous.position === position ? previous : { id, position });
            }}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                setDrop((previous) => previous?.id === id ? undefined : previous);
              }
            }}
            onDrop={(event) => {
              if (!dragged.current) return;
              event.preventDefault();
              if (!busy && pinned.has(dragged.current) === isPinned) onMove(dragged.current, id, dropPosition(event));
              endDrag();
            }}
            onDragEnd={endDrag}
          >
            <button type="button" disabled={busy} className={`topic-link ${selected === id ? "selected" : ""}`}
              aria-current={selected === id ? "page" : undefined} onClick={() => onSelect(id)}
              onKeyDown={(event) => {
                if (!event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
                event.preventDefault();
                const up = event.key === "ArrowUp";
                const neighbor = topics[index + (up ? -1 : 1)];
                if (neighbor && pinned.has(neighbor.topic_id) === isPinned) {
                  const button = event.currentTarget;
                  onMove(id, neighbor.topic_id, up ? "before" : "after");
                  requestAnimationFrame(() => button.focus({ preventScroll: true }));
                }
              }}>
              <span className="topic-dot" />
              <span>{label}</span>
            </button>
            <button type="button" className="sidebar-topic-pin" disabled={busy}
              aria-label={t("主题置顶状态 {0}", label)} aria-pressed={isPinned}
              title={pinLabel} onClick={() => onPin(id)}>
              <Icon name="pin" size={16} />
            </button>
          </div>
        );
      })}
      {!topics.length && <p className="sidebar-hint">{t("创建第一个追踪主题")}</p>}
    </div>
  );
}
