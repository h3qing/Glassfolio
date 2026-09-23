"""Command-line entry point: `glassfolio <command>`.

Writes that import files show a preview and ask before committing.
"""

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from glassfolio import broker_import, etf_import, market_import, recon, registry
from glassfolio.config import data_home
from glassfolio.exposure import company_exposure, exposure_lines
from glassfolio.keys import create_key, load_key
from glassfolio.lake import Lake, list_ops, open_lake, restore
from glassfolio.parsing import printable

ICONS = {"pass": "✓", "warn": "!", "fail": "✗"}


def _key_from_stdin() -> str:
    """The desktop app passes the key on stdin (never argv or env, which other processes can read)."""
    from glassfolio.keys import DbKeyError, validate_key

    try:
        return validate_key(sys.stdin.readline().strip())
    except DbKeyError as exc:
        sys.exit(f"error: {exc}")


def verify_recovery_key(home: Path, text: str) -> str:
    """Normalise a typed recovery key and prove it opens the data at `home`."""
    import duckdb

    from glassfolio.keys import DbKeyError, validate_key

    key = validate_key("".join(text.split()).lower())
    try:
        open_lake(home, key).con.close()
    except duckdb.Error:
        raise DbKeyError("that key doesn't open the data in this folder") from None
    return key


def cmd_key_restore(args) -> None:
    import getpass

    from glassfolio.keys import DbKeyError, store_key

    home = data_home()
    if not (home / "catalog.duckdb").exists():
        sys.exit(f"no Glassfolio data found at {home}")
    try:
        key = verify_recovery_key(home, getpass.getpass("Recovery key (input hidden): "))
    except DbKeyError as exc:
        sys.exit(f"error: {exc}")
    store_key(key, replace_existing=args.replace)
    print("The key is back in the Keychain; Glassfolio can open your data again.")


def _lake(key: str | None = None) -> Lake:
    return open_lake(data_home(), key or load_key())


def _confirm(args, question: str) -> bool:
    if args.yes:
        return True
    return input(f"{question} [y/N] ").strip().lower() == "y"


def _money(value: float | None) -> str:
    return f"{value:>14,.2f}" if value is not None else f"{'—':>14}"


def cmd_init(args) -> None:
    key = create_key()
    open_lake(data_home(), key)
    print(f"Created encrypted database at {data_home()}")
    print("Recovery key (store it OFFLINE; without it the data cannot be recovered):")
    print(f"\n    {key}\n")


def cmd_owner_add(args) -> None:
    from glassfolio.server.tax_api import profile_from
    from glassfolio.tax_profiles import add_tax_profile, assign_tax_profile

    lake = _lake()
    profile = profile_from({"state": args.state, "state_rate": args.state_rate,
                            "federal_ltcg_rate": args.ltcg, "federal_ordinary_rate": args.ordinary,
                            "name": f"{args.nickname} ({args.state})"})
    print(registry.add_owner(lake, args.nickname))
    assign_tax_profile(lake, add_tax_profile(lake, profile), owner=args.nickname)


def cmd_account_add(args) -> None:
    print(registry.add_account(_lake(), args.nickname, args.owner, args.broker, args.type))


def cmd_profile_add(args) -> None:
    mapping = json.loads(Path(args.mapping).read_text())
    print(registry.add_profile(_lake(), args.broker, mapping))


def cmd_import_statement(args) -> None:
    lake = _lake()
    p = broker_import.preview_statement(lake, Path(args.file), args.account, args.profile, args.as_of)
    print(f"Statement as of {p.as_of}  ({len(p.matches)} rows)")
    for m in p.matches:
        master = printable(m.master_name) or (
            "NEW SECURITY" if m.status == "new" else "(no name on file yet)")
        print(f"  {m.status:<8} {printable(m.row.symbol):<10} "
              f"file: {printable(m.row.description):<32} master: {master}")
    print(f"  total at export prices: {_money(float(p.total_value)).strip()}")
    if p.duplicate:
        sys.exit("This file was already imported.")
    if _confirm(args, "Check the security names above. Import?"):
        print(broker_import.commit_statement(lake, p))
        if args.delete_source:
            Path(args.file).unlink()
            print("Imported; the original is stored encrypted and the plaintext file was deleted.")
        else:
            print("Imported; the original is stored encrypted. Delete the plaintext file "
                  "(or rerun with --delete-source).")


def cmd_import_etf(args) -> None:
    lake = _lake()
    outstanding = Decimal(args.shares_outstanding) if args.shares_outstanding else None
    p = etf_import.preview_etf_holdings(lake, Path(args.file), args.etf, args.format,
                                        args.as_of, outstanding)
    weight = sum(r.weight or 0 for r in p.holdings.rows)
    print(f"{p.etf_ticker} holdings as of {p.holdings.as_of}: {len(p.holdings.rows)} rows, "
          f"weights {weight:.2%}, shares outstanding "
          f"{'present' if p.holdings.shares_outstanding else 'missing (weight fallback)'}")
    for error in p.errors:
        print(f"  ✗ {printable(error)}")
    if p.errors:
        sys.exit("Rejected; the previous version stays in force.")
    if _confirm(args, "Publish this version?"):
        print(etf_import.commit_etf_holdings(lake, p))


def cmd_import_lots(args) -> None:
    from glassfolio.tax_profiles import import_lots

    print(import_lots(_lake(), Path(args.file), args.account, args.as_of))


def cmd_taxes(args) -> None:
    from glassfolio.tax import portfolio_after_tax, position_taxes

    lake = _lake()
    for p in position_taxes(lake, args.as_of):
        if p.value:
            print(f"{printable(p.account):<18} {printable(p.ticker) or '?':<8} {p.treatment:<9} "
                  f"{_money(p.value)} tax {_money(p.tax)}  {p.basis}")
    t = portfolio_after_tax(lake, args.as_of)
    print(f"Before tax {_money(t.pre_tax).strip()}, tax {_money(t.tax).strip()}, "
          f"after tax {_money(t.after_tax).strip()} (planning estimate, not tax advice)")


def cmd_import_prices(args) -> None:
    print(market_import.import_prices(_lake(), Path(args.file), args.source))


def cmd_import_actions(args) -> None:
    print(market_import.import_corporate_actions(_lake(), Path(args.file)))


def cmd_proxy(args) -> None:
    registry.set_proxy(_lake(), args.ticker, args.proxy)
    print(f"{args.ticker} → {args.proxy}")


def cmd_exposure(args) -> None:
    lake = _lake()
    rows = company_exposure(lake, args.as_of, args.ticker, args.group_by)
    total = sum(l.value or 0 for l in exposure_lines(lake, args.as_of) if l.kind == "position")
    print(f"{'ticker':<8} {'group':<16} {'direct':>14} {'via funds':>14} {'total':>14} {'%':>7}")
    for r in rows:
        pct = f"{r.total / total:>7.2%}" if total else f"{'':>7}"
        flags = (" ≈" if r.approx else "") + (" ✗ missing price" if r.missing_price else "")
        print(f"{printable(r.ticker) or '?':<8} {printable(r.group):<16} {_money(r.direct_value)} "
              f"{_money(r.via_fund_value)} {_money(r.total)} {pct}{flags}")
    print("≈ approximate (weights or index proxy)")


def cmd_check(args) -> None:
    lake = _lake()
    account_id = None
    if args.account:
        acct = registry.find_account(lake.con, args.account)
        if acct is None:
            sys.exit(f"unknown account: {args.account}")
        account_id = acct.account_id
    report = recon.run_checks(lake, args.as_of, account_id, args.reported_total, args.reported_cost)
    print(f"{ICONS[report.status]} {report.scope} as of {report.as_of}: {report.status}")
    for r in report.results:
        detail = f" expected {_money(r.expected).strip()} got {_money(r.actual).strip()}" \
            if r.expected is not None else ""
        hint = f" — {printable(r.hint)}" if r.hint else ""
        print(f"  {ICONS[r.status]} {r.check_type}{detail}{hint}")


def cmd_ops(args) -> None:
    for op in list_ops(_lake(), args.limit):
        print(f"{op.op_id}  {op.ts:%Y-%m-%d %H:%M}  {op.actor:<9} {op.tool:<22} "
              f"+{op.rows_inserted} -{op.rows_deleted}  snap {op.snapshot_before}→{op.snapshot_after}")


def cmd_restore(args) -> None:
    if _confirm(args, f"Restore all tables to before {args.op_id}? Later changes are undone too."):
        print(restore(_lake(), args.op_id))


def cmd_config_tiingo(args) -> None:
    import getpass

    from glassfolio.keys import store_tiingo_token

    store_tiingo_token(getpass.getpass("Tiingo API token (input hidden): "))
    print("Saved to the macOS Keychain.")


def _fetch(lake: Lake, start: date, end: date, budget: int) -> None:
    from glassfolio.keys import load_tiingo_token
    from glassfolio.price_fetch import refresh_prices

    token = load_tiingo_token()
    if not token:
        print("No Tiingo token; skipping price fetch (run `glassfolio config tiingo`).")
        return
    r = refresh_prices(lake, start, end, token, budget, actor="user")
    print(f"Prices: {len(r.fetched)} fetched, {len(r.failed)} failed"
          + (f", {len(r.flagged)} flagged for review" if r.flagged else "")
          + (f", rate limited ({len(r.skipped)} left for next run)" if r.rate_limited else ""))


def cmd_prices_fetch(args) -> None:
    _fetch(_lake(), args.start, args.end, args.budget)


def cmd_daily(args) -> None:
    from datetime import timedelta

    from glassfolio.snapshots import snapshot_day

    lake = _lake()
    today = date.today()
    _fetch(lake, today - timedelta(days=7), today, args.budget)
    latest = lake.con.execute("SELECT max(date) FROM prices WHERE source = 'tiingo'").fetchone()[0]
    day = latest or today
    snapshot_day(lake, day, rebuild=True, actor="scheduler")
    report = recon.run_checks(lake, day, actor="scheduler")
    print(f"Snapshot for {day}; checks: {ICONS[report.status]} {report.status}")


def cmd_snapshot(args) -> None:
    from glassfolio.snapshots import snapshot_day

    op = snapshot_day(_lake(), args.date, rebuild=args.rebuild)
    print(op or f"{args.date} already has a snapshot (use --rebuild to recompute)")


def cmd_inbox(args) -> None:
    from glassfolio.flows import list_inbox

    for item in list_inbox(_lake()):
        p = item.payload
        if item.type == "unexplained_flow":
            pairs = "; ".join(f"pair with {c['item_id']} ({printable(c['account'])})"
                              for c in item.pair_candidates)
            print(f"{item.item_id}  {printable(p['account'])}: {_money(p['amount']).strip()} "
                  f"unexplained between {p['start']} and {p['end']}  {pairs}")
        else:
            print(f"{item.item_id}  {item.type}: {printable(json.dumps(p))}")


def cmd_inbox_answer(args) -> None:
    from glassfolio.flows import resolve_flow

    print(resolve_flow(_lake(), args.item_id, args.classification, args.pair, args.remember))


def cmd_changes(args) -> None:
    from glassfolio.attribution import company_attribution

    rows = company_attribution(_lake(), args.start, args.end)
    print(f"{'ticker':<8} {'start':>14} {'price':>14} {'your money':>14} {'rebalancing':>14} {'end':>14}")
    for r in rows:
        print(f"{printable(r.ticker) or '?':<8} {_money(r.start_value)} {_money(r.price_effect)} "
              f"{_money(r.flow_effect)} {_money(r.rebalance_effect)} {_money(r.end_value)}"
              + (" ≈" if r.approx else ""))


def cmd_returns(args) -> None:
    from glassfolio.performance import returns

    r = returns(_lake(), args.start, args.end)
    pct = lambda x: "—" if x is None else f"{x:.2%}"  # noqa: E731
    print(f"{r.start} → {r.end}: TWR {pct(r.twr)}, MWR (annualised) {pct(r.mwr)}, "
          f"net flows {_money(r.net_flows).strip()}")
    if r.open_questions:
        print(f"! {r.open_questions} unexplained cash flows are unanswered; see `glassfolio inbox`")


def cmd_config_model(args) -> None:
    from glassfolio.llm import list_models
    from glassfolio.settings import choose_model, load_settings

    url = args.url or load_settings().url
    available = list_models(url)
    if args.name is None:
        print(f"Server {url}: {', '.join(available) if available else 'not reachable'}")
        print("Choose one with: glassfolio config model --name <model>")
        return
    s = choose_model(url, args.name or None)
    note = "" if not s.name or s.name in available else " (not listed by the server yet)"
    print(f"Using {s.name or 'no model (built-in rules)'} at {s.url}{note}")


def cmd_eval_model(args) -> None:
    from glassfolio.llm import OpenAICompatModel
    from glassfolio.model_eval import run_eval, summary
    from glassfolio.settings import configured_model, load_settings, record_eval

    model = None if args.heuristic else (
        OpenAICompatModel(args.model, args.url or load_settings().url) if args.model else configured_model())
    if model is None and not args.heuristic:
        sys.exit("no model configured; use --model NAME, --heuristic, or `glassfolio config model`")
    results = run_eval(model)
    for r in results:
        print(f"  {ICONS['pass' if r.passed else 'fail']} {r.file:<32} {r.seconds:>6}s  {'; '.join(r.problems[:2])}")
    s = summary(results)
    print(f"{model.name if model else 'built-in rules'}: {s['passed']}/{s['total']} in {s['seconds']}s")
    if model is not None:
        record_eval(model.name, s)


def cmd_eval_assistant(args) -> None:
    from glassfolio.assistant.evals import run_assistant_eval
    from glassfolio.llm import OpenAICompatModel
    from glassfolio.settings import configured_model, load_settings, record_eval

    model = OpenAICompatModel(args.model, args.url or load_settings().url) if args.model else configured_model()
    if model is None:
        sys.exit("no model configured; use --model NAME or `glassfolio config model`")
    print(f"Asking {model.name} the evaluation questions on a synthetic portfolio (this takes a while)…", flush=True)
    results = run_assistant_eval(model, tuple(args.only or ()))
    for r in results:
        print(f"  {ICONS['pass' if r.passed else 'fail']} {r.id:<20} {r.seconds:>6}s  {'; '.join(r.problems)}", flush=True)
    passed = sum(r.passed for r in results)
    print(f"{model.name}: {passed}/{len(results)} questions answered correctly")
    record_eval(f"assistant:{model.name}", {"passed": passed, "total": len(results),
                                            "seconds": round(sum(r.seconds for r in results), 1),
                                            "failed": [r.id for r in results if not r.passed]})


def cmd_import_file(args) -> None:
    """Assisted import: the local model (or built-in rules) reads the file; you confirm."""
    from dataclasses import replace

    from glassfolio.settings import configured_model
    from glassfolio.understand import read_file, remember_reading

    lake = _lake()
    raw = Path(args.file).read_bytes()
    reading, errors = read_file(lake, raw, configured_model())
    print(f"{reading.kind} ({reading.source}), header on line {reading.header_row}, as of {reading.as_of}")
    for field, column in reading.columns.items():
        if column:
            print(f"  {field:<15} ← {printable(column)}")
    if reading.cash_symbols:
        print(f"  cash rows: {printable(', '.join(reading.cash_symbols))}")
    if errors:
        sys.exit("Can't read it cleanly: " + "; ".join(errors) + " — use the web UI to fix the columns.")
    if reading.kind != "positions":
        sys.exit("Lot details and fund holdings: use the web UI Import page to confirm them.")
    as_of = args.as_of or reading.as_of
    if as_of is None:
        sys.exit("no date found in the file; pass --as-of")
    p = broker_import.preview_statement(lake, raw, args.account, reading.profile_id, as_of, mapping=reading.mapping())
    for m in p.matches:
        print(f"  {m.status:<8} {printable(m.row.symbol):<12} {printable(m.master_name) or 'NEW SECURITY'}")
    print(f"  total at the file's prices: {_money(float(p.total_value)).strip()}")
    if p.duplicate:
        sys.exit("This file was already imported.")
    if _confirm(args, "Import?"):
        if reading.source != "saved":
            p = replace(p, profile_id=remember_reading(lake, reading, raw, args.broker))
        print(broker_import.commit_statement(lake, p))


def cmd_mcp(args) -> None:
    from glassfolio.assistant.mcp_server import WARNING, serve_stdio

    if not args.client_is_local:
        sys.exit(WARNING + "\nIf that is the case, start it with --client-is-local.")
    lake = _lake()

    def today():
        row = lake.con.execute("SELECT max(as_of_date) FROM import_files WHERE kind = 'statement'").fetchone()
        return row[0] or date.today()

    serve_stdio(lake, today)


def cmd_serve(args) -> None:
    from glassfolio.server.app import serve

    key = _key_from_stdin() if args.key_stdin else None
    serve(_lake(key), args.port, open_browser=not args.no_browser and not args.shell, shell=args.shell)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="glassfolio", description="See through your portfolio.")
    sub = p.add_subparsers(dest="command", required=True)
    day = date.fromisoformat

    def add(name, fn, *arguments, parent=sub):
        cmd = parent.add_parser(name)
        for flags, kwargs in arguments:
            cmd.add_argument(*flags, **kwargs)
        cmd.set_defaults(fn=fn)
        return cmd

    yes = (("-y", "--yes"), {"action": "store_true", "help": "skip confirmation"})
    add("init", cmd_init)
    owner = sub.add_parser("owner").add_subparsers(required=True)
    add("add", cmd_owner_add, (("nickname",), {}),
        (("--state",), {"default": "CA", "help": "two-letter state code (default CA)"}),
        (("--state-rate",), {"type": float, "help": "fraction, e.g. 0.093; needed for progressive states"}),
        (("--ltcg",), {"type": float, "default": 0.15, "help": "federal long-term gains rate"}),
        (("--ordinary",), {"type": float, "default": 0.24, "help": "federal income bracket"}),
        parent=owner)
    account = sub.add_parser("account").add_subparsers(required=True)
    add("add", cmd_account_add, (("nickname",), {}), (("--owner",), {"required": True}),
        (("--broker",), {"required": True}),
        (("--type",), {"required": True, "choices": registry.ACCOUNT_TYPES}), parent=account)
    profile = sub.add_parser("profile").add_subparsers(required=True)
    add("add", cmd_profile_add, (("broker",), {}), (("mapping",), {}), parent=profile)
    imp = sub.add_parser("import").add_subparsers(required=True)
    add("statement", cmd_import_statement, (("file",), {}), (("--account",), {"required": True}),
        (("--profile",), {"required": True}), (("--as-of",), {"type": day, "required": True}),
        (("--delete-source",), {"action": "store_true",
                                "help": "delete the plaintext file after a successful import"}),
        yes, parent=imp)
    add("etf", cmd_import_etf, (("file",), {}), (("--etf",), {"required": True}),
        (("--format",), {"choices": etf_import.FORMATS, "default": "generic"}),
        (("--as-of",), {"type": day}), (("--shares-outstanding",), {}), yes, parent=imp)
    add("prices", cmd_import_prices, (("file",), {}), (("--source",), {"default": "manual"}),
        parent=imp)
    add("actions", cmd_import_actions, (("file",), {}), parent=imp)
    add("file", cmd_import_file, (("file",), {}), (("--account",), {"required": True}),
        (("--as-of",), {"type": day}), (("--broker",), {}), yes, parent=imp)
    add("lots", cmd_import_lots, (("file",), {}), (("--account",), {"required": True}),
        (("--as-of",), {"type": day, "required": True}), parent=imp)
    add("taxes", cmd_taxes, (("--as-of",), {"type": day, "default": date.today()}))
    add("proxy", cmd_proxy, (("ticker",), {}), (("proxy",), {}))
    add("exposure", cmd_exposure, (("--ticker",), {}), (("--as-of",), {"type": day, "default": date.today()}),
        (("--group-by",), {"choices": ("account", "fund")}))
    add("check", cmd_check, (("--as-of",), {"type": day, "default": date.today()}),
        (("--account",), {}), (("--reported-total",), {"type": float}),
        (("--reported-cost",), {"type": float}))
    add("ops", cmd_ops, (("--limit",), {"type": int, "default": 30}))
    add("restore", cmd_restore, (("op_id",), {}), yes)
    key_cmd = sub.add_parser("key").add_subparsers(required=True)
    add("restore", cmd_key_restore, (("--replace",), {"action": "store_true",
        "help": "overwrite a different key already in the Keychain"}), parent=key_cmd)
    config = sub.add_parser("config").add_subparsers(required=True)
    add("tiingo", cmd_config_tiingo, parent=config)
    add("model", cmd_config_model, (("--url",), {"help": "OpenAI-compatible local server"}),
        (("--name",), {"help": "model to use; empty string for none"}), parent=config)
    prices = sub.add_parser("prices").add_subparsers(required=True)
    add("fetch", cmd_prices_fetch, (("--start",), {"type": day, "required": True}),
        (("--end",), {"type": day, "default": date.today()}),
        (("--budget",), {"type": int, "default": 45}), parent=prices)
    add("snapshot", cmd_snapshot, (("--date",), {"type": day, "default": date.today()}),
        (("--rebuild",), {"action": "store_true"}))
    add("daily", cmd_daily, (("--budget",), {"type": int, "default": 45}))
    inbox = sub.add_parser("inbox")
    inbox.set_defaults(fn=cmd_inbox)
    inbox_sub = inbox.add_subparsers()
    add("answer", cmd_inbox_answer, (("item_id",), {}),
        (("classification",), {"choices": ("deposit", "withdrawal", "transfer", "dividend",
                                           "not_a_flow")}),
        (("--pair",), {"help": "the other account's question, for a transfer"}),
        (("--remember",), {"action": "store_true", "help": "apply to this account from now on"}),
        parent=inbox_sub)
    add("changes", cmd_changes, (("--start",), {"type": day, "required": True}),
        (("--end",), {"type": day, "default": date.today()}))
    add("returns", cmd_returns, (("--start",), {"type": day, "required": True}),
        (("--end",), {"type": day, "default": date.today()}))
    add("eval-model", cmd_eval_model, (("--model",), {}), (("--url",), {}),
        (("--heuristic",), {"action": "store_true", "help": "evaluate the built-in rules instead"}))
    add("eval-assistant", cmd_eval_assistant, (("--model",), {}), (("--url",), {}),
        (("--only",), {"nargs": "*", "help": "case ids to run"}))
    add("mcp", cmd_mcp, (("--client-is-local",), {"action": "store_true",
                                                    "help": "confirm the MCP client uses a model on this Mac"}))
    add("serve", cmd_serve, (("--port",), {"type": int, "default": 8765, "help": "0 picks a free port"}),
        (("--no-browser",), {"action": "store_true"}),
        (("--key-stdin",), {"action": "store_true", "help": "read the database key from stdin (desktop app)"}),
        (("--shell",), {"choices": ("tauri",), "help": "running inside the desktop app"}))
    return p


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        args.fn(args)
    except (ValueError, OSError) as exc:
        sys.exit(f"error: {printable(str(exc))}")


if __name__ == "__main__":
    main()
