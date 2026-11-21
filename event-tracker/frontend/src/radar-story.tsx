import { useLayoutEffect, useRef, type ReactNode } from "react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

gsap.registerPlugin(ScrollTrigger);

type Metric = { value: number; label: string; open: () => void };

export function RadarStory({
  metrics,
  children,
}: {
  metrics: Metric[];
  children: ReactNode;
}) {
  const root = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const story = root.current!;
    const motion = gsap.matchMedia();
    motion.add(
      "(min-width: 701px) and (prefers-reduced-motion: no-preference)",
      () => {
        const signal = story.querySelector<HTMLElement>(".traveling-signal")!;
        const slots = Array.from(
          story.querySelectorAll<HTMLElement>("[data-radar-point]"),
        );
        const chapters = Array.from(
          story.querySelectorAll<HTMLElement>(".radar-chapter"),
        );

        const measure = () => {
          const origin = story.getBoundingClientRect();
          return {
            top: origin.top + window.scrollY,
            points: slots.map((slot) => {
              const box = slot.getBoundingClientRect();
              return {
                x: box.left + box.width / 2 - origin.left,
                y: box.top + box.height / 2 - origin.top,
              };
            }),
          };
        };
        let layout = measure();
        let maxScroll = ScrollTrigger.maxScroll(window);
        const draw = () => {
          const { top, points } = layout;
          const first = points[0].y;
          const last = points[points.length - 1].y;
          const offset = window.innerHeight * 0.56;
          const startScroll = Math.max(0, top + first - offset);
          const endScroll = Math.min(maxScroll, top + last - offset);
          const scrollProgress = gsap.utils.clamp(
            0,
            1,
            (window.scrollY - startScroll) /
              Math.max(1, endScroll - startScroll),
          );
          const cursor = gsap.utils.interpolate(first, last, scrollProgress);
          const next = points.findIndex((point) => point.y > cursor);
          const end = next === -1 ? points.length - 1 : Math.max(next, 1);
          const a = points[end - 1],
            b = points[end];
          const progress = gsap.utils.clamp(0, 1, (cursor - a.y) / (b.y - a.y));
          const drift = Math.sin(Math.PI * progress) * 24;
          gsap.set(signal, {
            x: gsap.utils.interpolate(a.x, b.x, progress) - 16 + drift,
            y: gsap.utils.interpolate(a.y, b.y, progress) - 16,
            scale:
              end === 1 ? gsap.utils.interpolate(1.5, 0.85, progress) : 0.85,
            rotation:
              end === 3 ? 90 + 45 * progress : 90 * (end - 2 + progress),
            borderRadius: `${end === 1 ? 50 : end === 2 ? 50 - 30 * progress : 20}%`,
            opacity: 1,
          });
        };
        const refresh = () => {
          layout = measure();
          maxScroll = ScrollTrigger.maxScroll(window);
          draw();
        };
        const track = ScrollTrigger.create({
          trigger: story,
          start: "top bottom",
          end: "bottom top",
          onUpdate: draw,
          onRefresh: refresh,
        });
        const resize = new ResizeObserver(refresh);
        resize.observe(story);
        draw();

        chapters.forEach((chapter) => {
          gsap.from(
            chapter.querySelectorAll(".chapter-copy > *, .chapter-content"),
            {
              y: 18,
              opacity: 0.55,
              stagger: 0.065,
              duration: 0.48,
              ease: "power2.out",
              clearProps: "opacity,transform",
              scrollTrigger: { trigger: chapter, start: "top 88%", once: true },
            },
          );
        });
        return () => {
          resize.disconnect();
          track.kill();
          signal.removeAttribute("style");
        };
      },
    );
    return () => motion.revert();
  }, []);

  return (
    <div className="radar-story" ref={root}>
      <section className="radar-hero" aria-label="追踪概况">
        <div className="radar-hero-copy">
          <span className="radar-caption">
            <i /> TOPIC MONITOR
          </span>
          <h2>追踪概况</h2>
          <div className="radar-metrics">
            {metrics.map(({ value, label, open }) => (
              <button key={label} onClick={open}>
                <strong>{String(value).padStart(2, "0")}</strong>
                <span>{label}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="radar-field" aria-hidden="true">
          <div className="radar-dial">
            <div className="radar-sweep" />
            <i className="radar-echo echo-first" />
            <i className="radar-echo echo-second" />
            <i className="radar-echo echo-third" />
            <span className="radar-origin" data-radar-point />
          </div>
        </div>
      </section>
      {children}
      <span className="traveling-signal" aria-hidden="true" />
    </div>
  );
}

export function RadarChapter({
  tone,
  title,
  label,
  onOpen,
  children,
}: {
  tone: "signal" | "evidence" | "review";
  title: string;
  label: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`radar-chapter chapter-${tone}`}>
      <span className="chapter-port" data-radar-point aria-hidden="true" />
      <div className="chapter-copy">
        <h2>{title}</h2>
        <button className="chapter-link" onClick={onOpen}>
          {label}
        </button>
      </div>
      <div className="chapter-content">{children}</div>
    </section>
  );
}
