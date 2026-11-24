import { useEffect, useMemo, useState } from "react";
import type { Topic } from "./types";

const storageKey = "traceradar-topic-order";
type Order = { pinned: string[]; unpinned: string[] };

function loadOrder(): Order {
  const readIds = (value: unknown) => Array.isArray(value)
    ? [...new Set(value.filter((id): id is string => typeof id === "string"))]
    : [];
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? "null");
    return { pinned: readIds(saved?.pinned), unpinned: readIds(saved?.unpinned) };
  } catch {
    return { pinned: [], unpinned: [] };
  }
}

function visibleOrder(topics: Topic[], saved: Order): Order {
  const newestFirst = topics.map((topic) => topic.topic_id).reverse();
  const available = new Set(newestFirst);
  const pinned = saved.pinned.filter((id) => available.has(id));
  const known = new Set([...saved.pinned, ...saved.unpinned]);
  return {
    pinned,
    unpinned: [
      ...newestFirst.filter((id) => !known.has(id)),
      ...saved.unpinned.filter((id) => available.has(id) && !pinned.includes(id)),
    ],
  };
}

export function useTopicOrder(topics: Topic[]) {
  const [saved, setSaved] = useState(loadOrder);
  const { orderedTopics, pinned } = useMemo(() => {
    const order = visibleOrder(topics, saved);
    const byId = new Map(topics.map((topic) => [topic.topic_id, topic]));
    return {
      orderedTopics: [...order.pinned, ...order.unpinned].map((id) => byId.get(id)!),
      pinned: new Set(order.pinned),
    };
  }, [topics, saved]);

  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify(saved));
  }, [saved]);

  function togglePin(id: string) {
    setSaved((previous) => {
      const order = visibleOrder(topics, previous);
      return order.pinned.includes(id)
        ? { pinned: order.pinned.filter((item) => item !== id), unpinned: [id, ...order.unpinned] }
        : { pinned: [id, ...order.pinned], unpinned: order.unpinned.filter((item) => item !== id) };
    });
  }

  function moveTopic(id: string, target: string, position: "before" | "after") {
    setSaved((previous) => {
      const order = visibleOrder(topics, previous);
      const group = order.pinned.includes(id) ? "pinned" : "unpinned";
      if (id === target || !order[group].includes(target) || !order[group].includes(id)) return previous;
      const ids = order[group].filter((item) => item !== id);
      ids.splice(ids.indexOf(target) + (position === "after" ? 1 : 0), 0, id);
      return { ...order, [group]: ids };
    });
  }

  function forgetTopic(id: string) {
    setSaved((previous) => ({
      pinned: previous.pinned.filter((item) => item !== id),
      unpinned: previous.unpinned.filter((item) => item !== id),
    }));
  }

  return { orderedTopics, pinned, togglePin, moveTopic, forgetTopic };
}
