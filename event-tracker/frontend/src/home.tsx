import { EventRadar } from "./radar";
import { useTranslation } from "./reference-translation";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { CoverImage } from "./covers";
import { t } from "./i18n";
import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { Icon } from "./ui";
import { status, longDate, topicTitle, topicDescription } from "./display";
import type { Topic } from "./types";
import { tagExamples, tagLabel, tagOptions } from "./topic-tags";

export function topicUrl(id: string) {
  const url = new URL(location.href);
  if (id) url.searchParams.set("topic", id);
  else url.searchParams.delete("topic");
  if (id && url.hash === "#projects-title") url.hash = "";
  return url.pathname + url.search + url.hash;
}

export function BrandLink({ onHome, current }: { onHome: () => void; current?: boolean }) {
  return (
    <a
      href={topicUrl("")}
      className="brand"
      aria-label={t("TraceRadar 首页")}
      aria-current={current ? "page" : undefined}
      onClick={(event) => follow(event, onHome)}
    >
      <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
      <span className="brand-wordmark" aria-hidden="true">
        <span>Trace</span><span>Radar</span><span className="brand-period">.</span>
      </span>
    </a>
  );
}

function follow(event: MouseEvent<HTMLAnchorElement>, open: () => void) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  open();
}

gsap.registerPlugin(ScrollTrigger);

type HomeProps = {
  topics: Topic[];
  loading: boolean;
  busy: boolean;
  feedback: ReactNode;
  preferences: ReactNode;
  onHome: () => void;
  onTopic: (id: string) => void;
  onNew: (tag?: string, example?: string) => void;
  onSettings: () => void;
};

export function ProjectHome({
  topics, loading, busy, feedback, preferences, onHome, onTopic, onNew, onSettings,
}: HomeProps) {
  const tr = useTranslation();
  const stage = useRef<HTMLDivElement>(null);
  const reel = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const [category, setCategory] = useState("");
  const tags = tagOptions(topics);
  const activeCategory = tags.includes(category) ? category : "";
  const items = [...topics].reverse().filter((topic) =>
    !activeCategory || topic.tags?.some((tag) => tag.toLocaleLowerCase() === activeCategory.toLocaleLowerCase()));
  const examples = tagExamples(activeCategory);

  useLayoutEffect(() => {
    if (reel.current) reel.current.scrollLeft = 0;
  }, [activeCategory]);

  useEffect(() => {
    const element = reel.current;
    if (!element) return;
    function update() {
      if (!element) return;
      const midpoint = element.scrollLeft + element.clientWidth / 2;
      element.querySelectorAll("li").forEach((frame) => {
        const position = (frame.offsetLeft + frame.offsetWidth / 2 - midpoint) / Math.max(1, element.clientWidth / 2);
        const relative = Math.max(-1.1, Math.min(1.1, position));
        frame.style.setProperty("--reel-angle", `${relative * -21}deg`);
        frame.style.setProperty("--reel-shift", `${relative * 42}px`);
      });
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
  }, [items.length, activeCategory]);

  useLayoutEffect(() => {
    const motion = gsap.matchMedia(stage);
    motion.add("(prefers-reduced-motion: no-preference)", () => {
      const intro = gsap.timeline({ defaults: { duration: .62, ease: "power3.out", clearProps: "opacity,transform,clipPath" } });
      intro.from(".home-intro > *", { y: 22, opacity: 0, stagger: .07 })
        .from(".live-radar", { clipPath: "inset(9% 7% 9% 7%)", opacity: .4 }, .04);
      gsap.from(".projects-heading, .reel, .reel-empty", {
        y: 26, opacity: 0, duration: .62, stagger: .09, ease: "power3.out",
        clearProps: "opacity,transform",
        scrollTrigger: { trigger: ".projects-section", start: "top 91%", once: true },
      });
      const scroller = reel.current;
      if (!scroller || scroller.scrollWidth <= scroller.clientWidth) return;
      gsap.utils.toArray<HTMLElement>(".frame-cover .cover-print").forEach((print) => {
        gsap.fromTo(print, { xPercent: -2, scale: 1.04 }, {
          xPercent: 2, scale: 1.04, ease: "none",
          scrollTrigger: { trigger: print.closest("li"), scroller, horizontal: true,
            start: "left right", end: "right left", scrub: true },
        });
      });
    });
    return () => motion.revert();
  }, [loading, items.length, activeCategory]);

  function roll(direction: number) {
    const frame = reel.current?.querySelector("li");
    if (!frame) return;
    reel.current?.scrollBy({
      left: direction * (frame.offsetWidth + 16),
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    });
  }

  return (
    <div className="project-home" ref={stage}>
      <header className="home-header">
        <BrandLink onHome={onHome} current />
        <nav aria-label={t("工作台工具")}>
          {preferences}
          <button className="home-utility" onClick={onSettings}>
            <Icon name="settings" size={17} /><span>{t("模型配置")}</span>
          </button>
          <button className="home-create" onClick={() => onNew(activeCategory || undefined)} disabled={busy}>
            <Icon name="plus" size={17} />{t("新建主题")}
          </button>
        </nav>
      </header>
      <main className="home-content">
        {feedback}
        <section className="home-hero" aria-labelledby="home-title">
          <div className="home-intro">
            <p className="home-kicker"><span /> {t("事件追踪工作台")}</p>
            <h1 id="home-title" tabIndex={-1}>{t("让进展，")}<br /><em>{t("有迹可循。")}</em></h1>
            <p className="home-description">{t("按主题追踪新闻，按时间查看事件进展和证据。")}</p>
            <a className="browse-topics" href="#projects-title">{t("浏览主题")}</a>
          </div>
          <EventRadar topics={topics} onTopic={onTopic} />
        </section>

        <section className="projects-section" aria-labelledby="projects-title">
          <div className="projects-heading">
            <div>
              <h2 id="projects-title">{t("追踪主题")} <span>{items.length}</span></h2>
              {!!topics.length && <p>{t("选择一个主题，查看正在跟进的事件。")}</p>}
            </div>
            {!!items.length && (
              <div className="reel-controls">
                <button type="button" aria-label={t("向左浏览主题")} aria-controls="topic-reel"
                  disabled={edges.start} onClick={() => roll(-1)}><Icon name="left" /></button>
                <button type="button" aria-label={t("向右浏览主题")} aria-controls="topic-reel"
                  disabled={edges.end} onClick={() => roll(1)}><Icon name="right" /></button>
              </div>
            )}
          </div>
          <label className="topic-filter">
            <span>{t("主题类型")}</span>
            <select value={activeCategory} onChange={(event) => setCategory(event.target.value)}>
              <option value="">{t("全部类型")}</option>
              {tags.map((tag) => <option key={tag} value={tag}>{tagLabel(tag)}</option>)}
            </select>
          </label>
          {loading ? (
            <p className="reel-empty" role="status">{t("正在读取追踪主题…")}</p>
          ) : items.length ? (
            <div className="reel" id="topic-reel" ref={reel}>
              <ul className="reel-track">
                {items.map((topic) => {
                  return (
                    <li key={topic.topic_id}>
                      <a className="project-frame" href={topicUrl(topic.topic_id)}
                        onClick={(event) => follow(event, () => onTopic(topic.topic_id))}>
                        <CoverImage topic={topic} className="frame-cover" />
                        <div className="frame-copy">
                          <span className={`frame-status ${topic.status}`}><i aria-hidden="true" />{status(topic.status)}</span>
                          <h3>{tr(topicTitle(topic.title))}</h3>
                          <p>{tr(topicDescription(topic.description))}</p>
                          {!!topic.tags?.length && <div className="frame-tags">{topic.tags.map((tag) => <span key={tag}>{tagLabel(tag)}</span>)}</div>}
                          <time dateTime={topic.updated_at}>{t("更新于")} {longDate(topic.updated_at)}</time>
                        </div>
                      </a>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : (
            <div className="reel-empty">
              <h3>{t(activeCategory ? "此类型还没有主题" : "还没有追踪主题")}</h3>
              <p>{t("新建一个主题开始追踪。")}</p>
            </div>
          )}
          {!!examples.length && <div className="topic-examples">
            <h3>{t("主题示例")}</h3>
            <ul>{examples.map((example) => <li key={example}>
              <button type="button" disabled={busy} onClick={() => onNew(activeCategory, example)}>{example}</button>
            </li>)}</ul>
          </div>}
        </section>
      </main>
    </div>
  );
}
