// Minimal line icons in the spirit of SF Symbols (1.6px strokes, 20px grid).
// Decorative: every use sits next to a text label or carries an aria-label.
const P = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" } as const;

const paths: Record<string, React.ReactNode> = {
  own: <><circle cx="10" cy="10" r="7" {...P} /><path d="M10 3v7l5 4.5" {...P} /></>,
  changes: <><path d="M3 14l4-4 3 3 7-7" {...P} /><path d="M13 6h4v4" {...P} /></>,
  questions: <><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h7A2.5 2.5 0 0 1 16 5.5v5a2.5 2.5 0 0 1-2.5 2.5H9l-3.5 3v-3A2.5 2.5 0 0 1 4 10.5z" {...P} /><path d="M8.6 6.6a1.5 1.5 0 1 1 2 1.4c-.4.2-.6.5-.6.9M10 11h.01" {...P} /></>,
  accounts: <><circle cx="7.5" cy="7" r="2.5" {...P} /><path d="M3 16c.6-2.6 2.3-4 4.5-4s3.9 1.4 4.5 4" {...P} /><circle cx="14" cy="7.5" r="2" {...P} /><path d="M13.5 12c1.8 0 3 1.1 3.5 3.3" {...P} /></>,
  import: <><path d="M10 3v9M6.5 8.5 10 12l3.5-3.5" {...P} /><path d="M3.5 13v1.5A2.5 2.5 0 0 0 6 17h8a2.5 2.5 0 0 0 2.5-2.5V13" {...P} /></>,
  reconcile: <><path d="M10 2.8l1.8 1.4 2.3-.2.7 2.2 2 1.2-.6 2.2.6 2.2-2 1.2-.7 2.2-2.3-.2L10 17.2l-1.8-1.4-2.3.2-.7-2.2-2-1.2.6-2.2-.6-2.2 2-1.2.7-2.2 2.3.2z" {...P} /><path d="M7.3 10.2l1.8 1.8 3.6-3.8" {...P} /></>,
  activity: <><circle cx="10" cy="10" r="7" {...P} /><path d="M10 6v4l2.8 1.8" {...P} /></>,
  eye: <><path d="M2.5 10S5.2 4.8 10 4.8 17.5 10 17.5 10 14.8 15.2 10 15.2 2.5 10 2.5 10z" {...P} /><circle cx="10" cy="10" r="2.4" {...P} /></>,
  eyeOff: <><path d="M4.2 6.3C3 7.6 2.5 10 2.5 10s2.7 5.2 7.5 5.2c1.4 0 2.6-.4 3.6-1M8 5.1c.6-.2 1.3-.3 2-.3 4.8 0 7.5 5.2 7.5 5.2s-.6 1.2-1.8 2.5" {...P} /><path d="M3 3l14 14" {...P} /></>,
  calendar: <><rect x="3" y="4.5" width="14" height="12" rx="3" {...P} /><path d="M3 8.5h14M7 3v3M13 3v3" {...P} /></>,
  chevron: <path d="M6 8l4 4 4-4" {...P} />,
};

export function Icon({ name, size = 18, label }: { name: keyof typeof paths | string; size?: number; label?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" aria-hidden={label ? undefined : true}
      role={label ? "img" : undefined} aria-label={label} className="icon">
      {paths[name]}
    </svg>
  );
}
