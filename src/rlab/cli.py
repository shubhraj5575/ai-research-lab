"""rlab command-line interface.

    rlab run      – execute an autonomous research session
    rlab demo     – full-length demonstration session (>= 20 iterations)
    rlab report   – generate the evidence-grounded paper for a session
    rlab serve    – launch the research dashboard
    rlab verify   – re-run sampled experiments and compare result hashes
    rlab sessions – list recorded research sessions
    rlab graph    – export a session's provenance graph
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import LabConfig
from .events import EventBus
from .jsonlog import configure_logging, get_logger
from .store import Store


def _build(cfg: LabConfig, store: Store, bus: EventBus):
    from .agents import ResearchDirector
    return ResearchDirector(bus=bus, cfg=cfg, store=store)


# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    cfg = LabConfig(
        root=Path(args.root),
        max_iterations=args.iterations,
        seeds_per_config=args.seeds,
        max_parallel_workers=args.workers,
        wall_budget_minutes=args.budget_minutes,
        executor=args.executor,
        docker_image=args.docker_image,
        offline_corpus=args.offline_corpus,
        reasoner=args.reasoner,
        llm_provider=args.llm_provider or "",
        bootstrap_iters=args.bootstrap_iters,
    )
    store = Store(cfg.db_path)
    bus = EventBus()
    log = get_logger("cli")
    director = _build(cfg, store, bus)

    t0 = time.time()
    ctx = director.start_session(args.domain, question=args.question,
                                 title=args.title)
    log.info("session_started", extra={"session_id": ctx.session_id,
                                       "question": ctx.question})
    summary = director.run_session(ctx, max_iterations=args.iterations,
                                   wall_budget_minutes=args.budget_minutes)
    elapsed = time.time() - t0
    print(f"\n=== session {summary['session_id']} ===")
    print(f"iterations executed : {summary['iterations']} "
          f"({elapsed / 60:.1f} min)")
    for o in summary["outcomes"]:
        line = (f"  it{o['iteration']:>3}: {o['status']:<18}"
                f" verdict={o['verdict'] or '-':<7}"
                f" hypothesis={o['hypothesis_status'] or '-'}")
        if o.get("detail"):
            line += f"  [{o['detail']}]"
        print(line)
    print(f"\nNext steps:")
    print(f"  rlab report {summary['session_id']}")
    print(f"  rlab serve   # then open http://{cfg.host}:{cfg.port}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    args.iterations = args.iterations or 22
    args.domain = args.domain or "bandit"
    args.title = args.title or "Overnight autonomous demonstration"
    return cmd_run(args)


def cmd_report(args: argparse.Namespace) -> int:
    cfg = LabConfig(root=Path(args.root))
    store = Store(cfg.db_path)
    from .reports.paper import PaperGenerator

    gen = PaperGenerator(cfg, store)
    out_dir = Path(args.out) if args.out else Path(args.root) / args.session_id / "report"
    artifacts = gen.generate(args.session_id, out_dir)
    print(f"paper    : {artifacts.markdown_path}")
    print(f"claims   : {artifacts.claims_path}")
    print(f"figures  : {len(artifacts.figure_files)} SVG files in {artifacts.figures_dir}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = LabConfig(root=Path(args.root), host=args.host, port=args.port)
    from .server.app import run_server

    print(f"dashboard on http://{cfg.host}:{cfg.port}  (Ctrl-C to stop)")
    run_server(cfg)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = LabConfig(root=Path(args.root))
    store = Store(cfg.db_path)
    exp = store.get_experiment(args.experiment_id)
    if exp is None:
        print(f"experiment {args.experiment_id!r} not found", file=sys.stderr)
        return 2
    from .domain import get_domain
    from .runtime.runner import ExperimentRunner
    from .sandbox import make_executor

    plugin = get_domain(exp.config.domain)
    runner = ExperimentRunner(store, cfg, make_executor(cfg.executor,
                                                        image=cfg.docker_image),
                              EventBus())
    report = runner.verify_reproducibility(exp, plugin, sample_size=args.samples)
    print(f"checked {report['checked']} sampled runs; "
          f"{report['passed']} reproduced identical hashes")
    for d in report["details"]:
        mark = "OK " if d["match"] else "FAIL"
        print(f"  [{mark}] {d['variant']} seed={d['seed']} "
              f"ref={d['ref_hash']} new={d['new_hash']}")
    return 0 if report["passed"] == report["checked"] else 1


def cmd_sessions(args: argparse.Namespace) -> int:
    cfg = LabConfig(root=Path(args.root))
    store = Store(cfg.db_path)
    rows = store.list_sessions()
    if not rows:
        print("no sessions recorded")
        return 0
    print(f"{'session id':<22} {'created':<17} {'domain':<10} "
          f"{'status':<18} title")
    for s in rows:
        created = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["created_at"]))
        print(f"{s['id']:<22} {created:<17} {s['domain']:<10} "
              f"{s['status']:<18} {(s['title'] or '')[:44]}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    cfg = LabConfig(root=Path(args.root))
    store = Store(cfg.db_path)
    from .graph.research_graph import ResearchGraph

    g = ResearchGraph(store, args.session_id)
    problems = g.validate()
    if problems:
        print("validation warnings:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
    payload = g.to_json() | {"validation": problems}
    if args.format == "graphml":
        text = g.to_graphml()
    else:
        import json

        text = json.dumps(payload, indent=2)
    target = Path(args.out) if args.out else None
    if target:
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target}")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rlab",
        description="AI Research Lab - autonomous computational research environment",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--root", default="runs",
                       help="workspace directory holding lab.db and artifacts")

    run_p = sub.add_parser("run", help="run an autonomous research session")
    common(run_p)
    run_p.add_argument("--domain", default="bandit",
                       help="research domain plugin (bandit|optim)")
    run_p.add_argument("--question", default=None,
                       help="override the domain's default research question")
    run_p.add_argument("--title", default="")
    run_p.add_argument("--iterations", type=int, default=12)
    run_p.add_argument("--seeds", type=int, default=30,
                       help="paired repetitions per configuration")
    run_p.add_argument("--workers", type=int, default=4)
    run_p.add_argument("--budget-minutes", type=float, default=45.0)
    run_p.add_argument("--executor", choices=["local", "docker"], default="local")
    run_p.add_argument("--docker-image", default="rlab-sandbox:latest")
    run_p.add_argument("--offline-corpus", action="store_true",
                       help="skip live arXiv discovery; use bundled corpus")
    run_p.add_argument("--reasoner", choices=["heuristic", "llm"], default="heuristic")
    run_p.add_argument("--llm-provider", choices=["anthropic", "openai"],
                       default=None)
    run_p.add_argument("--bootstrap-iters", type=int, default=2000)
    run_p.set_defaults(func=cmd_run)

    demo_p = sub.add_parser("demo", help="long-form demonstration session")
    common(demo_p)
    demo_p.add_argument("--domain", default="bandit")
    demo_p.add_argument("--iterations", type=int, default=22)
    demo_p.add_argument("--seeds", type=int, default=30)
    demo_p.add_argument("--workers", type=int, default=6)
    demo_p.add_argument("--budget-minutes", type=float, default=90.0)
    demo_p.add_argument("--offline-corpus", action="store_true")
    demo_p.add_argument("--bootstrap-iters", type=int, default=2000)
    demo_p.add_argument("--executor", choices=["local", "docker"], default="local")
    demo_p.add_argument("--docker-image", default="rlab-sandbox:latest")
    demo_p.add_argument("--reasoner", choices=["heuristic", "llm"], default="heuristic")
    demo_p.add_argument("--llm-provider", choices=["anthropic", "openai"], default=None)
    demo_p.set_defaults(func=cmd_demo)

    rep_p = sub.add_parser("report", help="generate paper for a session")
    common(rep_p)
    rep_p.add_argument("session_id")
    rep_p.add_argument("--out", default=None)
    rep_p.set_defaults(func=cmd_report)

    srv_p = sub.add_parser("serve", help="launch the dashboard")
    common(srv_p)
    srv_p.add_argument("--host", default="127.0.0.1")
    srv_p.add_argument("--port", type=int, default=8620)
    srv_p.set_defaults(func=cmd_serve)

    ver_p = sub.add_parser("verify", help="re-run sampled runs, compare hashes")
    common(ver_p)
    ver_p.add_argument("experiment_id")
    ver_p.add_argument("--samples", type=int, default=2)
    ver_p.set_defaults(func=cmd_verify)

    ses_p = sub.add_parser("sessions", help="list recorded sessions")
    common(ses_p)
    ses_p.set_defaults(func=cmd_sessions)

    g_p = sub.add_parser("graph", help="export a session's provenance graph")
    common(g_p)
    g_p.add_argument("session_id")
    g_p.add_argument("--format", choices=["json", "graphml"], default="json")
    g_p.add_argument("--out", default=None)
    g_p.set_defaults(func=cmd_graph)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
