import type { Status } from "./api";

const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const pct = new Intl.NumberFormat("en-US", { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const fmtPct = (x: number) => (Number.isFinite(x) ? pct.format(x) : "—");

/** An amount; frosted over in privacy mode (ratios stay visible). */
export function Amount({ value }: { value: number | null | undefined }) {
  return <span className="amount">{value == null ? "—" : money.format(value)}</span>;
}

const ICON: Record<Status, string> = { pass: "✓", warn: "!", fail: "✗" };
const LABEL: Record<Status, string> = { pass: "Reconciled", warn: "Check notes", fail: "Doesn't match" };

export function StatusBadge({ status, label }: { status: Status; label?: string }) {
  return (
    <span className={`status ${status}`}>
      <span aria-hidden>{ICON[status]}</span> {label ?? LABEL[status]}
    </span>
  );
}

export const accountTypeLabel: Record<string, string> = {
  taxable: "Taxable", traditional_ira: "Traditional IRA", roth_ira: "Roth IRA", "401k": "401(k)",
  hsa: "HSA", "529": "529 plan", other: "Other",
};

export const treatmentLabel: Record<string, string> = {
  taxable: "Taxable", deferred: "Tax-deferred", exempt: "Tax-exempt",
};

export const pctInput = (x: number | null | undefined) => (x == null ? "" : String(Math.round(x * 10000) / 100));
