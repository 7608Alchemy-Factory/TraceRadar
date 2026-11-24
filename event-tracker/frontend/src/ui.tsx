import React, { useLayoutEffect, useRef } from "react";

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    grid: (
      <>
        <rect x="3" y="3" width="6" height="6" />
        <rect x="15" y="3" width="6" height="6" />
        <rect x="3" y="15" width="6" height="6" />
        <rect x="15" y="15" width="6" height="6" />
      </>
    ),
    events: (
      <>
        <path d="M4 5h16M4 12h16M4 19h10" />
        <circle cx="18" cy="19" r="2" />
      </>
    ),
    evidence: (
      <>
        <path d="M5 3h10l4 4v14H5zM14 3v5h5M8 12h8M8 16h6" />
      </>
    ),
    review: (
      <>
        <path d="m12 3 9 17H3zM12 9v4M12 16v1" />
      </>
    ),
    sources: (
      <>
        <circle cx="6" cy="6" r="3" />
        <circle cx="18" cy="6" r="3" />
        <circle cx="12" cy="18" r="3" />
        <path d="m7.5 8.5 3 7m6-7-3 7M9 6h6" />
      </>
    ),
    activity: (
      <>
        <path d="M3 12h4l3-8 4 16 3-8h4" />
      </>
    ),
    settings: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="m9 3 6 0 1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z" />
      </>
    ),
    plus: <path d="M12 5v14M5 12h14" />,
    arrow: <path d="M4 12h15m-6-6 6 6-6 6" />,
    left: <path d="m14 6-6 6 6 6" />,
    right: <path d="m10 6 6 6-6 6" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    check: <path d="m5 12 4 4L19 6" />,
    play: <path d="m8 4 12 8-12 8z" />,
    help: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M9 9a3 3 0 1 1 5 2c-2 1-2 2-2 3M12 17v1" />
      </>
    ),
    external: (
      <>
        <path d="M14 3h7v7m0-7L10 14M11 5H4v15h15v-7" />
      </>
    ),
    search: (
      <>
        <circle cx="10" cy="10" r="6" />
        <path d="m15 15 6 6" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.grid}
    </svg>
  );
}

export function Modal({
  title,
  onClose,
  children,
  wide = false,
  sheet = false,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
  sheet?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const exit = useRef<Animation | null>(null);
  useLayoutEffect(() => {
    const d = ref.current!;
    const opener = document.activeElement;
    d.showModal();
    return () => {
      exit.current?.cancel();
      d.close();
      if (opener instanceof HTMLElement && opener.isConnected)
        opener.focus({ preventScroll: true });
    };
  }, []);
  function dismiss() {
    const d = ref.current!;
    if (exit.current) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      d.close();
      onClose();
      return;
    }
    d.dataset.closing = "";
    exit.current = d.animate(
      [
        { opacity: 1, transform: "none" },
        {
          opacity: 0,
          transform: sheet
            ? "translateX(36px)"
            : "translateY(12px) scale(.985)",
        },
      ],
      { duration: 170, easing: "ease-in", fill: "forwards" },
    );
    void exit.current.finished.then(
      () => {
        d.close();
        onClose();
      },
      () => {},
    );
  }
  return (
    <dialog
      ref={ref}
      className={`dialog ${wide ? "wide" : ""} ${sheet ? "sheet" : ""}`}
      onCancel={(e) => {
        e.preventDefault();
        dismiss();
      }}
      onClick={(e) => {
        if (e.target === ref.current) dismiss();
      }}
      aria-label={title}
    >
      <div className="dialog-head">
        <div>
          <span className="kicker">TRACERADAR</span>
          <h2>{title}</h2>
        </div>
        <button className="icon-button" onClick={dismiss} aria-label="关闭窗口">
          <Icon name="close" />
        </button>
      </div>
      <div className="dialog-body">{children}</div>
    </dialog>
  );
}

export function Empty({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-mark">
        <Icon name="events" size={26} />
      </span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {children}
    </div>
  );
}
