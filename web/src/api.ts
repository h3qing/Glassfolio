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
  after_tax?: number; direct_after_tax?: number; via_fund_after_tax?: number;
}
export interface Summary {
  total_value: number; cash_value: number; other_value: number;
  approx_value: number; missing_prices: number;
  after_tax: number; tax: number; missing_cost: number;
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
export interface Returns {
  start: string; end: string; start_value: number; end_value: number; net_flows: number;
  twr: number | null; mwr: number | null; mwr_period: number | null; open_questions: number;
  missing_prices: number;
}
export interface Attribution {
  ticker: string | null; name: string | null; start_value: number; end_value: number;
  price_effect: number; flow_effect: number; rebalance_effect: number; change: number; approx: boolean;
  missing_price: boolean;
}
export interface Changes {
  start: string; end: string; returns: Returns; companies: Attribution[];
  after_tax: { start: number; end: number };
}
export type Basis = "pre" | "after";
export interface TaxProfile {
  tax_profile_id: string | null; name: string; federal_ltcg_rate: number; federal_ordinary_rate: number;
  niit: boolean; state: string; state_rate: number; withdrawal_rate: number | null;
  no_lot_assumption: "short_term" | "long_term"; count_losses: boolean;
  rates: { ltcg: number; stcg: number; withdrawal: number };
}
export interface StateDefault { code: string; name: string; rate: number | null; note: string; as_of: string }
export interface AfterTaxTotals {
  pre_tax: number; tax: number; after_tax: number; missing_cost: number;
  by_treatment: Record<string, { pre_tax: number; tax: number }>;
}
export interface PositionTax {
  account: string; ticker: string | null; treatment: string; value: number; cost: number | null;
  lt_gain: number; st_gain: number; tax: number; after_tax: number; basis: string; profile: string;
}
export interface TaxOverview {
  as_of: string; states: StateDefault[]; default_profile: TaxProfile; profiles: TaxProfile[];
  people: { owner: string; profile_id: string | null }[];
  accounts: { nickname: string; owner: string; account_type: string; default_treatment: string;
    treatment: string | null; profile_id: string | null }[];
  totals: AfterTaxTotals; what_if: AfterTaxTotals; positions: PositionTax[];
}
export type ScenarioQuery = Partial<Record<"ltcg" | "ordinary" | "state_rate" | "withdrawal" | "niit" | "assumption", string>>;
export interface InboxItem {
  item_id: string; type: string; status: string; created_at: string;
  payload: Record<string, any>;
  pair_candidates: { item_id: string; account: string; amount: number }[];
}
export type FileKind = "positions" | "lots" | "fund_holdings";
export interface Reading {
  token: string; kind: FileKind; header_row: number; columns: Record<string, string | null>;
  as_of: string | null; cash_symbols: string[]; skip_symbols: string[]; broker: string | null;
  fund_ticker: string | null; shares_outstanding: number | null; weight_is_percent: boolean;
  source: "saved" | "model" | "heuristic" | "user"; profile_id: string | null; header: string[];
  fields: Record<FileKind, string[]>; errors: string[]; sample: string[][]; lines: string[];
}
export interface ModelInfo {
  url: string; name: string | null; available: string[]; reachable: boolean;
  evals: Record<string, { passed: number; total: number; seconds: number; failed: string[] }>;
}
export interface EvalRun {
  model: string | null; passed: number; total: number; seconds: number;
  results: { file: string; passed: boolean; problems: string[]; seconds: number }[];
}
export interface Slice { owner?: string; account_type?: string; broker?: string; account?: string }

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = { "x-glassfolio": "1", ...(init?.headers ?? {}) };
  const res = await fetch(path, { credentials: "same-origin", ...init, headers });
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
  changes: (start: string, end: string, s: Slice) => call<Changes>(`/api/changes?${query({ start, end, ...s })}`),
  history: (s: Slice) => call<{ date: string; value: number }[]>(`/api/history?${query({ ...s })}`),
  inbox: () => call<InboxItem[]>("/api/inbox"),
  answer: (body: { item_id: string; classification: string; pair?: string; remember?: boolean }) =>
    post<{ op_id: string }>("/api/inbox/answer", body),
  addOwner: (nickname: string) => post("/api/owners", { nickname }),
  addPerson: (body: Record<string, unknown>) => post("/api/people", body),
  taxes: (asOf: string, s: Slice, scenario: ScenarioQuery = {}) =>
    call<TaxOverview>(`/api/taxes?${query({ as_of: asOf, ...s, ...scenario })}`),
  saveProfile: (body: Record<string, unknown>) => post<{ tax_profile_id: string }>("/api/tax/profile", body),
  assignProfile: (body: { profile_id: string | null; owner?: string; account?: string }) => post("/api/tax/assign", body),
  setTreatment: (account: string, treatment: string | null) => post("/api/tax/treatment", { account, treatment }),
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
  models: () => call<ModelInfo>("/api/assist/models"),
  chooseModel: (url: string, name: string | null) => post("/api/assist/model", { url, name }),
  evaluate: () => post<EvalRun>("/api/assist/eval", {}),
  readFile: (file: File) => api.upload<Reading>("/api/assist/read", file, {}),
  previewReading: (body: Record<string, unknown>) => post<Record<string, any>>("/api/assist/preview", body),
};
