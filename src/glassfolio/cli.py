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


def _lake() -> Lake:
    return open_lake(data_home(), load_key())


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
    print(registry.add_owner(_lake(), args.nickname))


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


def cmd_serve(args) -> None:
    from glassfolio.server.app import serve

    serve(_lake(), args.port, open_browser=not args.no_browser)


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
    add("add", cmd_owner_add, (("nickname",), {}), parent=owner)
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
    add("proxy", cmd_proxy, (("ticker",), {}), (("proxy",), {}))
    add("exposure", cmd_exposure, (("--ticker",), {}), (("--as-of",), {"type": day, "default": date.today()}),
        (("--group-by",), {"choices": ("account", "fund")}))
    add("check", cmd_check, (("--as-of",), {"type": day, "default": date.today()}),
        (("--account",), {}), (("--reported-total",), {"type": float}),
        (("--reported-cost",), {"type": float}))
    add("ops", cmd_ops, (("--limit",), {"type": int, "default": 30}))
    add("restore", cmd_restore, (("op_id",), {}), yes)
    add("serve", cmd_serve, (("--port",), {"type": int, "default": 8765}),
        (("--no-browser",), {"action": "store_true"}))
    return p


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        args.fn(args)
    except (ValueError, OSError) as exc:
        sys.exit(f"error: {printable(str(exc))}")
