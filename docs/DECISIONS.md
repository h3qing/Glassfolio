# Implementation decisions

Choices made while building, including where the code departs from or refines
[SPEC.md](SPEC.md).

## Phase 1 (MVP)

- **DuckLake + encryption verified.** The DuckLake catalog is an encrypted DuckDB
  file (`METADATA_PARAMETERS MAP {'ENCRYPTION_KEY': …}`), and data files use `ENCRYPTED`.
  A wrong key fails to attach, Parquet files can't be read on their own, and small
  inserts are inlined into the encrypted catalog. Tested with DuckDB 1.5.5.
- **ops_log ↔ snapshot link.** Each write runs in one transaction that also appends
  its `ops_log` row and calls `set_commit_message(..., extra_info => op_id)`.
  `snapshot_after` is derived from the snapshot tagged with the op_id, so the log
  stays append-only.
- **Restore gotcha.** Inside a transaction, a DuckLake `AT (VERSION => n)` read issued
  after a `DELETE` on the same table returns nothing. `restore` therefore stages the old
  rows in a temp table before deleting. Covered by `test_restore_rolls_back_an_import`.
- **`positions.export_price`** (not in the spec's field list). Spec §11 says to
  reconcile with the export's prices and analyse with closes. Export prices are also
  written to `prices` with source `broker_export`, ranked below real closes and
  ETF-file prices on the same date.
- **`import_files`** gains `kind`, `as_of_date` and `raw_content`. The original file is
  kept inside the encrypted lake (spec §5.1.7), so the user can delete their copy.
- **Exposure output** also has `position` rows (top-level holdings) and `fund_residual`
  rows, so conservation can be checked from a single query.
- **Security identity.** IDs are opaque (`sec_…`). Matching tries FIGI, then ISIN, then
  CUSIP, and uses the ticker only when no stable identifier conflicts. OpenFIGI mapping
  comes later.
- **New securities default to `stock`.** Importing an ETF's holdings promotes it to
  `etf`. An ETF you hold without importing its holdings shows up as a single
  "company" until you import them.
- **Network:** besides market and ETF data sources, the allowlist must include
  `extensions.duckdb.org`, because the first run installs the `ducklake` extension.
- **Raw files:** the original statement is stored inside the encrypted lake. The
  plaintext copy is deleted only when you pass `--delete-source`, since deleting your
  files is never automatic.
- **`GLASSFOLIO_DB_KEY`** is for tests and development only. Environment variables can
  leak through shell history and process info, so real keys stay in the Keychain.
- **Untrusted text** from import files is stripped of control characters before
  printing, so a doctored file can't use terminal escape codes to fake the
  confirmation preview.
- **Look-through edge cases** (from code review, each covered by
  `tests/test_review_regressions.py`):
  - The same security on two statement rows is summed, and so is the same holding on
    two rows of a fund file.
  - A holdings version is identified by (etf, as_of, fetched_at), not by file hash, and
    re-importing a file with the same hash is refused.
  - Non-USD cash and non-equity rows (futures, swaps) are typed `other` and carried in
    dollars as `weight × p(fund)`. Futures contract counts would give notional value, so
    they are not used.
  - A fund that lists itself as a holding: the self-edge is dropped and that slice stays
    in the fund residual.
  - Duplicate corporate actions are ignored, both at import and in the query.
  - Duplicate and validation checks run again inside the write transaction, so a
    stale preview can't commit.
- **Not yet:** dbt (arrives with the incremental `portfolio_daily` in phase 3), network
  fetching of issuer files (`refresh_etf_holdings`), `inbox_items`, and the MCP
  server. The importers are already split into preview and commit, the shape those
  tools need.

## Early UI + phase 2

- **The UI came before phase 6** (the owner asked, because a CLI-only tool is hard
  to use and to check). The spec's form is kept: a local web page on 127.0.0.1,
  React, later packaged with Tauri.
- **Localhost guards:** the Host header must be ours (defeats DNS rebinding). The
  session cookie is HttpOnly and SameSite=Strict, and can only be set through the
  one-time token link. API requests must come from our own origin. Uploads are
  parsed from memory, so plaintext is never written to a temp file.
- **No web fonts or CDNs.** The UI uses the system SF Pro, so opening the UI makes
  no outbound requests.
- **Slicing** uses nicknames (person, account type, broker, account), and every
  view shares the same `Slice`.

## Phase 3

- **Attribution generalised.** Four look-throughs (see `attribution.py`) give price
  / your money / fund rebalancing. For one fund level this is exactly spec §4.2.
  It also works for nested funds and splits, and the three effects always sum to
  the change. Weight-only funds and index proxies are marked ≈ because their
  "baskets" move with prices.
- **Flows are derived, not stored** (a departure from the spec's `cash_flows` table,
  prompted by code review). An early version worked flows out once, at import time.
  Out-of-order imports, corrected statements, and dividends or prices that arrived
  later then left wrong flows behind. Now each pair of consecutive statements per
  account (a "period") is re-evaluated from current data on every read. Only the
  user's answers (`flow_answers`, keyed by account and period, latest answer wins,
  so answers can be changed) and rules (`flow_rules`) are stored.
- **Flow inference rules.** The first statement of an account is its starting
  balance. The latest import wins for a given date.
  - Below the threshold (max($500, 1% of value)), the difference is `inferred`.
  - Above it, a remembered rule for that account and direction classifies it;
    otherwise it becomes a question.
  - A holding sold between statements with no close after the earlier statement
    is a `data_gap` question. It is never valued at the old price.
  - Dividends count once per security and date.
  - Transfers pair two open questions from different accounts with opposite amounts
    (within 1%) whose dates are within 7 days.
- **Accounts opened mid-period** bring their opening value into returns as money
  added, not as gain.
- **Returns.** Periods start and end on statement dates, and flows are assumed to
  happen on those dates. TWR is cumulative for the period. MWR is annualised
  (XIRR), but periods under a year show the period figure, because annualising a
  short period gives absurd numbers. Open questions are reported alongside, since
  they make returns unreliable.
- **dbt is not used (yet).** The spec planned dbt-duckdb for the incremental
  `portfolio_daily`. It's one model with simple "compute once per day, append"
  semantics, so a small Python function (`snapshots.py`) does it without dbt-core's
  dependency weight. Revisit if the number of models grows.
- **`portfolio_daily.kind`** (added): security, cash, other or fund_residual, so a
  day's rows still add up to the portfolio.
- **Prices from Tiingo.** The token lives in the Keychain and is sent in the
  `Authorization` header, never in the URL or logs. Only `api.tiingo.com` over HTTPS
  is allowed. The free tier is about 50 requests per hour, so each run has a budget:
  held securities and funds first, then look-through constituents by size. The
  others keep the price from their fund's holdings file. Split factors become
  corporate actions, and dividends go to `prices.dividend_per_share`. A daily move
  beyond ±25% that no split explains opens a review question.
- **Repeat fetches** add no duplicate price rows or review questions.
- **Localhost hardening** (from security review):
  - The launch token works once and is exchanged for a separate session secret.
    The cookie is named after the port, because browsers don't separate cookies
    by port.
  - API calls need an `X-Glassfolio: 1` header (which forces a CORS preflight
    that is never approved), and `Sec-Fetch-Site` must be same-origin when present.
  - Pages send `X-Frame-Options: DENY` and a strict CSP.
  - Uploads stay in memory (Starlette's spooling to disk is disabled) and are capped
    at 20 MB before parsing.
  - The Tiingo client refuses redirects, since urllib would follow them to any host
    with the token header.
- **Scheduling** is left to the user (a launchd example is in the README). Glassfolio
  never installs system configuration by itself.

## Phase 4: taxes

Rates are planning assumptions, not tax advice. The UI says so wherever it shows tax.

- **Treatment by account type:**
  - Taxable accounts are taxable.
  - Traditional IRA and 401(k) are tax-deferred (value × (1 − withdrawal rate)).
  - Roth IRA, HSA and 529 are tax-exempt. HSA and 529 were added at the owner's
    request, assuming qualified use.
  - Any account can be overridden to taxable, deferred or exempt, which is how
    special cases are marked tax-exempt.
- **Profiles:** one per person, optionally overridden per account. The account's
  profile wins, then the person's, then the built-in California default: federal
  15% long-term and 24% ordinary, CA 9.3%, no NIIT. Effective rates:
  - long-term = federal long-term + NIIT + state
  - short-term = federal ordinary + NIIT + state
  - withdrawal = federal ordinary + state, or an explicit override
- **State dropdown when adding a person:**
  - No-income-tax states and flat-tax states are prefilled.
  - California is prefilled at the 9.3% bracket.
  - Progressive states must be entered for your bracket, because no single
    default would be honest.
  - Rates are the 2025 figures as I know them. They are marked "check yours".
- **Lots:** lots are used when their shares cover the position within 0.5%; held
  for more than 365 days counts as long-term. Otherwise the person's assumption
  applies: all short-term by default (conservative, as the owner asked), or all
  long-term. A position with lots that don't match is flagged
  "assumed (lots incomplete)".
- **Losses** reduce tax by default (the spec's formula). A per-person switch turns
  that off.
- **Missing cost basis:** the gain is left out and flagged. It is not guessed.
- **After-tax look-through:** each position's after-tax/pre-tax factor is applied
  to every line held through it. So NVDA inside a taxable QQQ position bears
  QQQ's tax, and NVDA inside a Roth doesn't.
- **What-if:** overrides are applied on top of every profile and never saved.
- **Attribution stays before tax.** After-tax attribution would mix tax changes
  into the price effect. The What changed view shows after-tax start and end
  values instead.

## Phase 5 (first part): LLM-assisted import

The owner rejected hand-written column mappings as "too old school". So files are
read by a local model, with fixed code checking whatever it proposes.

- **Model-agnostic.** The client speaks the OpenAI-compatible API (Ollama, LM
  Studio, llama.cpp, MLX servers, vLLM). You choose the server URL and model in
  Settings, and nothing is tied to one model.
  - Structured output is negotiated: JSON schema, then JSON mode, then JSON
    pulled out of free text.
  - Request fields a server rejects (for example `reasoning_effort`) are dropped
    and the request retried.
- **Loopback only.** The model URL must resolve to loopback. Proxies and redirects
  are ignored.
- **The model returns configuration only:** kind, header row, which header holds
  each field, date, cash and summary rows. The file's content is marked as
  untrusted data in the prompt.
- **Every answer is validated against the file itself.**
  - Proposed columns must exist; invented ones are dropped.
  - The header row snaps to the line that actually contains the proposed column
    names, since small models miscount lines.
  - Field mix-ups across kinds are accepted (e.g. `cost_basis` for lots' `cost`).
  - Shares × price must match market value.
  - A cash row must exist, and "cash" rows must look like cash by symbol or
    price, never by description, which is attacker-controlled text.
  - On failure, the validator's complaints go back to the model for one retry;
    then the heuristics take over.
- **Heuristics** (keyword matching) are the no-model tier. They read all 6
  synthetic evaluation files correctly.
- **Layouts are remembered by header fingerprint** (a hash of the normalized header
  row), stored with the import profile, so each broker's format needs the model
  only once. The date is re-read from each new file.
- **Evaluation set** (`evals/`): six synthetic files in different broker and fund
  styles, including a prompt-injection file. `glassfolio eval-model` runs it, and
  the score is saved in settings.json (no data in it). Results on the owner's Mac
  on 2026-09-23:

  | Model | Result |
  | --- | --- |
  | qwen3.6:27b | 6/6 |
  | qwen2.5:3b | 0/6 |
  | llama3.2:3b | 0/6 |

  The 3B models fail; the UI recommends 8B or larger, per spec §8.
- **Parser tolerance** for real exports: a blank line ends the holdings table,
  single-cell footer lines are ignored, and cost can come from cost per share ×
  shares.

## Phase 5 (second part): chat assistant and MCP

- **A JSON action protocol, not native tool calling.** Runtimes and models differ in
  tool-calling support, so each step the model returns `{"action": "call_tool" |
  "reply", ...}` through the same negotiated structured output as the importer. Any
  model that can produce JSON works. Up to 6 steps per turn.
- **Hardening from review:**
  - The assistant's run_checks only looks and saves nothing; MCP likewise.
  - The query sandbox runs in a child process that is killed at 5 s, because
    DuckDB can't interrupt every operation or bound every allocation. It also has
    a 512 MB memory limit and 1 thread.
  - Numbers the model itself supplied (SQL constants, echoed arguments) don't
    count as grounding, unless the person said them.
  - Only the person's messages count as a source; earlier assistant replies don't.
  - Tolerance is half a unit of the last digit shown, the sign must match, ×100
    applies only to percentages, and years are skipped only as bare numbers.
    Arithmetic tricks (`SELECT 48000 + 213.55`) and spelled-out numbers are
    residual gaps.
  - Proposals show the amount and the paired account, expire after 30 minutes,
    and are refused if the question was answered meanwhile. `remember` must be
    literally true.
  - Filters are matched to real values (friendly spellings accepted) or refused
    with the valid choices, instead of silently returning zeros. This fixed 2
    evaluation failures.
  - The reason text in ops_log has digits and number words masked.
- **Tools** (`assistant/tools.py`):
  - portfolio_summary, get_exposure, get_changes, list_accounts, list_questions,
    run_checks, tax_summary: typed, with numbers computed by SQL.
  - query_readonly: free SQL, sandboxed.
  - answer_question: write, proposal only.
  - request_user_file: shows a card.
- **The query_readonly sandbox:** a fresh in-memory DuckDB with external access
  off and configuration locked, holding copies of allow-listed tables. There are
  no keys, no raw imported files and no ops_log; `getenv` doesn't exist, and file
  access and ATTACH/INSTALL are refused.
  - The SQL must also parse as one SELECT with no table functions.
  - Results are capped at 200 rows, and queries time out after 5 seconds.
- **Writes are two-step.** answer_question returns a proposal; only the Confirm
  button executes it. It is logged with actor `model` and the model's stated
  reason, with digits masked so no amounts reach ops_log.
- **Grounding guard.** Every number in a reply must appear in this turn's tool
  results, the person's message or recent history. Dates, days and years are
  ignored, and percentages may appear ×100. A reply with other numbers is sent
  back once, then flagged to the person.
- **False-claim guard.** Found by testing with qwen3.6:27b, which once replied
  "I've noted that" without proposing anything. A reply that claims a change
  when nothing was proposed is sent back once, then flagged.
- **Tool results are labelled as untrusted data** in the conversation. The
  evaluation plants instructions inside a security name.
- **Evaluation** (`evals/assistant/`): 22 known-answer and safety cases on a
  throwaway lake with the synthetic portfolio, run with
  `glassfolio eval-assistant [--model]`. qwen3.6:27b on the owner's Mac: 18/22 at
  first. Two of those failures were eval bugs (curly apostrophe; the test account
  not counted), and two were silent empty filters, now refused. After the fixes,
  22/22 (see the run log in the commit message).
- **MCP server** (`glassfolio mcp --client-is-local`): stdio, read tools only.
  - It refuses to start without the flag, because an MCP client may be backed
    by a cloud model and the server can't tell (spec §7 red line).
  - It needs the lake to itself: stop `serve` first (DuckDB file lock).

## Phase 6: desktop app (Tauri)

- **Shape:**
  - The Rust shell creates the window, applies native Liquid Glass through
    `tauri-plugin-liquid-glass` (NSGlassEffectView on macOS 26, NSVisualEffectView
    earlier), and runs the unlock sequence.
  - The Python service is a child process; the webview loads its local URL, so the
    web UI and its security guards are unchanged.
  - The page is served with `data-shell="tauri"`. It then drops its own backdrop
    blur and lets the native glass show through, with room for the traffic lights.
- **Key transport:** the key goes to the service on stdin, never in argv or the
  environment, which other processes can read. The app keeps stdin open, and the
  service exits when it closes. Verified by force-killing the app (SIGKILL): the
  service followed.
- **Keychain:** the app uses the same item as the CLI (service `glassfolio`,
  account `db-key`). Created on first launch with 32 random bytes; the recovery
  key is shown once in a native dialog.
- **Touch ID:** LocalAuthentication, device-owner policy (Touch ID or the Mac's
  password), before the key is read. This is an app-enforced unlock, not hardware
  binding: binding the Keychain item to biometrics needs a Developer ID–signed app
  with a keychain access group. The switch lives in settings.json, so it deters
  casual access, not someone who already controls your user account.
- **Navigation lock:** the webview may load only its splash page and the service's
  exact origin. The service page gets exactly one Tauri permission,
  `core:window:allow-start-dragging`.
- **Development builds only** honor `GLASSFOLIO_DB_KEY` (a test key on synthetic
  data), like the CLI's tests. Release builds always use the Keychain.
- **Packaging:**
  - The service is a PyInstaller one-file binary (`glassfolio-server`) holding the
    web UI, SQL, evaluation sets and synthetic demo data.
  - The DuckLake extension ships as a Tauri resource, not through PyInstaller,
    because re-signing it would break DuckDB's signature. The app never downloads
    it.
  - The DMG is made with `hdiutil`, because Tauri's DMG script needs Finder
    automation.
  - Size: the app is 74 MB, the DMG 54 MB.
- **Hardening from security review:**
  - The service binds its port *before* announcing it. The app accepts only
    `http://127.0.0.1:<port>/?token=…` as the READY URL, and the window-drag
    capability is granted at runtime for that exact origin, not
    `127.0.0.1:*`.
  - The app watches the service and closes, with a message, if the service
    stops. Quitting closes the service's stdin first, then kills it after 3 s.
  - The app embeds the bundled service's SHA-256 at build time and refuses to
    hand the key to a different binary. The service gets an allowlisted
    environment (no DYLD_*, no GLASSFOLIO_STATIC), and the packaged service
    ignores GLASSFOLIO_STATIC.
  - The Keychain item is added with add-only semantics (never overwritten). If
    encrypted data exists but the key is missing, the app refuses to create a new
    key and points to `glassfolio key restore`, which checks the recovery key
    against the data before storing it.
  - Touch ID fails closed. Only a Mac with no password skips the prompt.
  - Residual: without Developer ID signing and hardened runtime, a program running
    as you that can rewrite the whole app bundle can still get the key. The hash
    check stops a swapped service binary, not a swapped app.
- **Not done:** Developer ID signing and notarization (needs an Apple developer
  account), auto-update, and Windows and Linux (the Keychain and Touch ID code is
  macOS-only).
