// Line icons (24 × 24, stroked with currentColor).
const PATHS = {
  pen: <path d="M4 20h4L19 9l-4-4L4 16z" />,
  fit: <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />,
  up: <path d="M12 19V5M6 11l6-6 6 6" />,
  chev: <path d="M6 9l6 6 6-6" />,
  check: <path d="M5 12l5 5 9-10" />,
  search: <><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></>,
  cube: <><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" /><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5" /></>,
  plan: <><rect x="4" y="4" width="16" height="16" rx="1" /><path d="M4 12h8V4M12 16v4" /></>,
  chat: <path d="M5 5h14v10H9l-4 4z" />,
  layers: <><path d="M12 4l9 5-9 5-9-5z" /><path d="M3 14l9 5 9-5" /></>,
  focus: <><path d="M12 4l9 5-9 5-9-5z" /><path d="M3 15l9 5 9-5" opacity=".35" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  trash: <path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" />,
  eye: <><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>,
  eyeOff: <><path d="M3 3l18 18" /><path d="M10.6 6.1A9.8 9.8 0 0 1 12 6c5 0 9 6 9 6a16 16 0 0 1-3.1 3.6M6.4 7.6C4.2 9.2 3 12 3 12s4 6 9 6a8.8 8.8 0 0 0 3.6-.8" /></>,
  target: <><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2.5" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></>,
  download: <path d="M12 4v11M7 10l5 5 5-5M5 20h14" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
};
export type IconName = keyof typeof PATHS;

export function Icon({ name, className }: { name: IconName; className?: string }) {
  return (
    <svg className={`icon${className ? ` ${className}` : ''}`} viewBox="0 0 24 24" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}
