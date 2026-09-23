// Typed client for the local Glassfolio API. The session cookie is set by the
// one-time token link printed by `glassfolio serve`.

export type Status = "pass" | "warn" | "fail";

export interface Account {
  nickname: string; owner: string; account_type: string; broker: string;
  currency: string; latest_statement: string | null;
}
export interface Meta {
  owners: string[]; accounts: Account[]; account_types: string[]; brokers: string[];
  profiles: { profile_id: string; broker: string }[];
  statement_dates: string[]; default_as_of: string;
}
export interface Company {
  ticker: string | null; name: string | null; group: string | null;
  direct_value: number; via_fund_value: number; total: number;
  approx: boolean; missing_price: boolean;
}
export interface Summary {
  total_value: number; cash_value: number; other_value: number;
  approx_value: number; missing_prices: number;
}
export interface Exposure { as_of: string; summary: Summary; companies: Company[] }
export interface CompanyDetail { ticker: string; fund: Company[]; account: Company[]; owner: Company[] }
export interface CheckRow {
  scope: string; account: string | null; check_type: string; as_of: string;
  expected: number | null; actual: number | null; diff: number | null;
  status: Status; hint: string | null; run_at: string;
}
export interface CheckResult {
  check_type: string; status: Status; expected: number | null; actual: number | null;
  hint: string | null; diff: number | null;
}
export interface CheckReport { scope: string; as_of: string; results: CheckResult[]; status: Status }
export interface Op {
  op_id: string; ts: string; actor: string; tool: string; description: string;
  rows_inserted: number; rows_deleted: number; snapshot_before: number; snapshot_after: number | null;
}
export interface Slice { owner?: string; account_type?: string; broker?: string; account?: string }

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(body.error ?? `request failed (${res.status})`);
  return body as T;
}

const query = (params: Record<string, string | undefined>) => {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => v && q.set(k, v));
  return q.toString();
};

const post = <T>(path: string, body: unknown) =>
  call<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

export const api = {
  meta: () => call<Meta>("/api/meta"),
  exposure: (asOf: string, s: Slice) => call<Exposure>(`/api/exposure?${query({ as_of: asOf, ...s })}`),
  company: (ticker: string, asOf: string, s: Slice) =>
    call<CompanyDetail>(`/api/company/${encodeURIComponent(ticker)}?${query({ as_of: asOf, ...s })}`),
  checks: () => call<CheckRow[]>("/api/checks"),
  runChecks: (body: { as_of: string; account?: string; reported_total?: string; reported_cost?: string }) =>
    post<CheckReport>("/api/checks", body),
  ops: () => call<Op[]>("/api/ops"),
  addOwner: (nickname: string) => post("/api/owners", { nickname }),
  addAccount: (a: { nickname: string; owner: string; broker: string; account_type: string }) =>
    post("/api/accounts", a),
  addProfile: (broker: string, mapping: string) =>
    post<{ profile_id: string }>("/api/profiles", { broker, mapping }),
  upload: <T>(path: string, file: File, fields: Record<string, string>) => {
    const form = new FormData();
    form.set("file", file);
    Object.entries(fields).forEach(([k, v]) => v && form.set(k, v));
    return call<T>(path, { method: "POST", body: form });
  },
  commit: (token: string) => post<{ op_id: string }>("/api/import/commit", { token }),
};
