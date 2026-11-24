import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Icon } from "./ui";
import { status, longDate } from "./display";
import type { Topic } from "./types";

export function topicUrl(id: string) {
  const url = new URL(location.href);
  if (id) url.searchParams.set("topic", id);
  else url.searchParams.delete("topic");
  return url.pathname + url.search + url.hash;
}

export function BrandLink({ onHome, current }: { onHome: () => void; current?: boolean }) {
  return (
    <a
      href={topicUrl("")}
      className="brand"
      aria-label="TraceRadar 首页"
      aria-current={current ? "page" : undefined}
      onClick={(event) => follow(event, onHome)}
    >
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
      TraceRadar<span className="brand-period">.</span>
    </a>
  );
}

function follow(event: MouseEvent<HTMLAnchorElement>, open: () => void) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  open();
}

function asciiArt(scene: number, columns: number, rows: number) {
  const shades = " .:+*#%@";
  return Array.from({ length: rows }, (_, row) =>
    Array.from({ length: columns }, (_, column) => {
      const x = (2 * column) / (columns - 1) - 1;
      const y = ((2 * row) / (rows - 1) - 1) * 0.88;
      const radius = Math.hypot(x, y);
      const angle = Math.atan2(y, x);
      const grid = row % 4 === 0 && column % 4 === 0 ? 1 : 0;
      if (scene === 1) {
        const height = y + 0.27 * Math.sin(4 * x + y) + 0.11 * Math.sin(12 * x - y);
        const wave = Math.abs(Math.sin(14 * height + 2 * x));
        return shades[wave < 0.36 ? Math.round((1 - Math.abs(y)) * (6 - wave * 8)) : grid];
      }
      if (scene === 2) {
        const ribbon = Math.abs(y - 0.5 * Math.sin(4 * x));
        const edge = Math.abs(ribbon - (0.25 + 0.12 * Math.cos(7 * x)));
        const fold = Math.max(0, 1 - edge * 8) * (3.5 + 3 * Math.cos(7 * x + 6 * y) ** 2);
        return shades[Math.max(grid, Math.round(fold))];
      }
      const sweep = Math.max(0, 1 - Math.abs(angle + 0.75) * 1.6);
      const ring = Math.abs(Math.sin(radius * 17)) < 0.32;
      const signal = Math.hypot(x - 0.38, y + 0.32) < 0.061 ||
        Math.hypot(x + 0.47, y - 0.27) < 0.057;
      if (signal) return "@";
      if (radius > 0.88) return shades[grid];
      if (Math.abs(x) < 0.018 || Math.abs(y) < 0.023) return "+";
      return shades[ring ? 3 + Math.round(sweep * 4) : Math.max(grid, Math.round(sweep * 4))];
    }).join(""),
  ).join("\n");
}

const radar = asciiArt(0, 75, 30);
const covers = [0, 1, 2].map((scene) => asciiArt(scene, 55, 20));

function coverFor(id: string) {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % covers.length;
}

type HomeProps = {
  topics: Topic[];
  loading: boolean;
  busy: boolean;
  feedback: ReactNode;
  onHome: () => void;
  onTopic: (id: string) => void;
  onNew: () => void;
  onDemo: () => void;
  onReplay: () => void;
  onSettings: () => void;
};

export function ProjectHome({
  topics, loading, busy, feedback, onHome, onTopic, onNew, onDemo, onReplay, onSettings,
}: HomeProps) {
  const reel = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const items = [...topics].reverse();

  useEffect(() => {
    const element = reel.current;
    if (!element) return;
    function update() {
      if (!element) return;
      setEdges({
        start: element.scrollLeft <= 2,
        end: element.scrollLeft + element.clientWidth >= element.scrollWidth - 2,
      });
    }
    const resize = new ResizeObserver(update);
    resize.observe(element);
    if (element.firstElementChild) resize.observe(element.firstElementChild);
    element.addEventListener("scroll", update, { passive: true });
    update();
    return () => {
      resize.disconnect();
      element.removeEventListener("scroll", update);
    };
  }, [topics.length]);

  function roll(direction: number) {
    const frame = reel.current?.querySelector("li");
    if (!frame) return;
    reel.current?.scrollBy({
      left: direction * (frame.offsetWidth + 16),
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    });
  }

  return (
    <div className="project-home">
      <header className="home-header">
        <BrandLink onHome={onHome} current />
        <nav aria-label="工作台工具">
          <button className="home-utility" onClick={onSettings}>
            <Icon name="settings" size={17} /><span>模型配置</span>
          </button>
          <button className="home-create" onClick={onNew} disabled={busy}>
            <Icon name="plus" size={17} />新建主题
          </button>
        </nav>
      </header>
      <main className="home-content">
        {feedback}
        <section className="home-hero" aria-labelledby="home-title">
          <div className="home-intro">
            <p className="home-kicker"><span /> 事件追踪工作台</p>
            <h1 id="home-title" tabIndex={-1}>让进展，<br /><em>有迹可循。</em></h1>
            <p className="home-description">按主题追踪新闻，按时间查看事件进展和证据。</p>
          </div>
          <div className="home-radar" aria-hidden="true">
            <div className="radar-coordinate radar-coordinate-top">+ <span>22°19′ N</span> +</div>
            <pre>{radar}</pre>
            <div className="radar-coordinate radar-coordinate-bottom">+ <span>114°10′ E</span> +</div>
          </div>
        </section>

        <section className="projects-section" aria-labelledby="projects-title">
          <div className="projects-heading">
            <div>
              <h2 id="projects-title">追踪主题 <span>{topics.length}</span></h2>
              {!!topics.length && <p>选择一个主题，查看正在跟进的事件。</p>}
            </div>
            {!!topics.length && (
              <div className="reel-controls">
                <button type="button" aria-label="向左浏览主题" aria-controls="topic-reel"
                  disabled={edges.start} onClick={() => roll(-1)}><Icon name="left" /></button>
                <button type="button" aria-label="向右浏览主题" aria-controls="topic-reel"
                  disabled={edges.end} onClick={() => roll(1)}><Icon name="right" /></button>
              </div>
            )}
          </div>
          {loading ? (
            <p className="reel-empty" role="status">正在读取追踪主题…</p>
          ) : topics.length ? (
            <div className="reel" id="topic-reel" ref={reel}>
              <ul className="reel-track">
                {items.map((topic) => {
                  const cover = coverFor(topic.topic_id);
                  return (
                    <li key={topic.topic_id}>
                      <a className="project-frame" href={topicUrl(topic.topic_id)}
                        onClick={(event) => follow(event, () => onTopic(topic.topic_id))}>
                        <div className={`frame-cover cover-${cover}`} aria-hidden="true">
                          <pre>{covers[cover]}</pre>
                        </div>
                        <div className="frame-copy">
                          <span className={`frame-status ${topic.status}`}><i aria-hidden="true" />{status(topic.status)}</span>
                          <h3>{topic.title}</h3>
                          <p>{topic.description}</p>
                          <time dateTime={topic.updated_at}>更新于 {longDate(topic.updated_at)}</time>
                        </div>
                      </a>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : (
            <div className="reel-empty">
              <h3>还没有追踪主题</h3>
              <p>可以新建一个感兴趣的主题，或用模拟新闻先体验一次。</p>
            </div>
          )}
        </section>

        <div className="home-tools">
          <button onClick={onDemo} disabled={busy || loading}><Icon name="play" size={17} />体验三步演示</button>
          <button onClick={onReplay} disabled={busy || loading}><Icon name="evidence" size={17} />准备新闻回放</button>
        </div>
      </main>
    </div>
  );
}
