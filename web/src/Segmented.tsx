/** Capsule segmented control; the glass knob slides to the selected segment. */
export function Segmented<T extends string>({ options, value, onChange, label }: {
  options: { id: T; label: string }[]; value: T; onChange: (v: T) => void; label: string;
}) {
  const index = Math.max(0, options.findIndex((o) => o.id === value));
  return (
    <div className="segmented" role="tablist" aria-label={label}
      style={{ "--count": options.length, "--index": index } as React.CSSProperties}>
      <span className="knob" aria-hidden />
      {options.map((o) => (
        <button key={o.id} role="tab" aria-selected={o.id === value} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
