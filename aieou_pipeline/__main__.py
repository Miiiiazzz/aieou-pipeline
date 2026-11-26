"""Command line:  python -m aieou_pipeline <plan|run|normalize|export|status>"""
from __future__ import annotations

import argparse

from . import config, db, export as export_mod, normalize as normalize_mod, prompts, runner


def cmd_plan(cfg, conn, args):
    total = prompts.load_xlsx(conn, cfg)
    cases = prompts.select_cases(conn, cfg["sample"])
    repeats = cfg["run"]["repeats"]
    before = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO jobs (job_id, case_id, surface, repeat) VALUES (?,?,?,?)",
            [(f"{s}|{c}|{r}", c, s, r)
             for c in cases for s in cfg["surfaces"] for r in range(1, repeats + 1)])
    after = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    print(f"Prompts in file: {total:,}   selected by sample.mode='{cfg['sample']['mode']}': {len(cases):,}")
    print(f"Surfaces: {len(cfg['surfaces'])}   repeats: {repeats}")
    print(f"Jobs added: {after - before:,}   total in queue: {after:,}\n")
    print(f"{'surface':<20}{'pending':>10}{'est. cost':>12}")
    grand = 0.0
    for name, surface in cfg["surfaces"].items():
        pending = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE surface=? AND status='pending'", (name,)).fetchone()[0]
        cost = pending * surface["price_per_1000"] / 1000
        grand += cost
        print(f"{name:<20}{pending:>10,}{cost:>11,.2f}$")
    pending_all = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()[0]
    print(f"{'total':<20}{pending_all:>10,}{grand:>11,.2f}$")
    for name, settings in cfg["providers"].items():
        per_hour = settings.get("searches_per_hour")
        if per_hour:
            print(f"At {per_hour:,} searches/hour ({name}): about {pending_all / per_hour:,.1f} hours")
    print("\nCost uses price_per_1000 from config.toml; check it against your plan.")


def cmd_status(cfg, conn, args):
    rows = conn.execute(
        "SELECT surface, status, COUNT(*) n FROM jobs GROUP BY surface, status ORDER BY 1, 2").fetchall()
    if not rows:
        print("No jobs yet. Run `plan` first.")
    for r in rows:
        print(f"{r['surface']:<20}{r['status']:<10}{r['n']:>8,}")
    errors = conn.execute(
        "SELECT last_error, COUNT(*) n FROM jobs WHERE status='failed' GROUP BY 1 ORDER BY n DESC LIMIT 5").fetchall()
    for e in errors:
        print(f"  failed x{e['n']}: {e['last_error']}")


def main():
    parser = argparse.ArgumentParser(prog="aieou_pipeline")
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--dry-run", action="store_true",
                        help="use mock responses and a separate data folder; no API calls, no cost")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plan", help="load prompts, queue jobs, show the cost estimate")
    run_p = sub.add_parser("run", help="send pending jobs")
    run_p.add_argument("--max-searches", type=int, default=0, help="stop after this many (0 = no cap)")
    run_p.add_argument("--surface", action="append", help="only this surface (repeatable)")
    run_p.add_argument("--retry-failed", action="store_true")
    norm_p = sub.add_parser("normalize", help="parse saved responses into tables")
    norm_p.add_argument("--rebuild", action="store_true", help="re-parse everything")
    sub.add_parser("export", help="write answers.csv and items.csv")
    sub.add_parser("status", help="show job counts")
    args = parser.parse_args()

    cfg = config.load(args.config, dry_run=args.dry_run)
    conn = db.connect(cfg["run"]["data_dir"])
    if args.command == "plan":
        cmd_plan(cfg, conn, args)
    elif args.command == "run":
        runner.run(cfg, conn, args.max_searches, args.surface, args.retry_failed)
    elif args.command == "normalize":
        normalize_mod.normalize(cfg, conn, args.rebuild)
    elif args.command == "export":
        export_mod.export(cfg, conn)
    elif args.command == "status":
        cmd_status(cfg, conn, args)


if __name__ == "__main__":
    main()
