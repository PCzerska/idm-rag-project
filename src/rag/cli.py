"""Interfejs wiersza poleceń: `uv run rag <komenda>` (Makefile tylko to opakowuje)."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from rag.config import CHUNKING_STRATEGIES, get_settings

console = Console()


def cmd_ingest(args: argparse.Namespace) -> None:
    from rag.ingest import ingest

    stats = ingest(
        [Path(p) for p in (args.paths or [get_settings().data_dir])],
        get_settings(),
        strategy=args.chunking,
        collection=args.collection,
        recreate=args.recreate,
    )
    console.print(
        f"✅ Zaindeksowano {stats['chunks']} chunków z {stats['files']} plików do "
        f"kolekcji [bold]{stats['collection']}[/] w {stats['total_s']}s "
        f"(embedding: {stats['embed_s']}s)"
    )


def cmd_ask(args: argparse.Namespace) -> None:
    from rag.generate import answer_question, best_quote

    result = answer_question(args.question, get_settings(), k=args.k, collection=args.collection)
    console.print(Markdown(result.answer.answer))
    if result.answered_from_context:
        console.print("\n[bold]Źródła cytowane:[/]")
        for n in result.cited_numbers:
            hit = result.hits[n - 1]
            quote = best_quote(hit.text, result.answer.answer)
            console.print(f"  [{n}] {hit.label()}  [dim](score {hit.score:.3f})[/]")
            console.print(f"      [italic]„{quote}”[/]")
        if not result.has_inline_citations:
            console.print("[yellow]Uwaga: brak znaczników [n] w tekście odpowiedzi.[/]")
    else:
        console.print(
            "\n[yellow]Model nie zacytował żadnego fragmentu – kontekst nie zawiera odpowiedzi.[/]"
        )
    if retrieved_only := result.retrieved_only():
        console.print("[bold]Pobrane (retrieved), niecytowane:[/]")
        for i, hit in retrieved_only:
            console.print(f"  [dim][{i}] {hit.label()}  (score {hit.score:.3f})[/]")
    console.print(
        f"[dim]retrieval {result.retrieve_s}s · generacja {result.generate_s}s · "
        f"structured output: {result.mode}[/]"
    )
    if args.show_context:
        for i, hit in enumerate(result.hits, start=1):
            console.rule(f"[{i}] {hit.label()}")
            console.print(hit.text)


def cmd_agent(args: argparse.Namespace) -> None:
    from rag.agent import AgentStep, run_agent

    def show(step: AgentStep) -> None:
        if step.kind == "tool_call":
            console.print(f"[cyan]🔧 {step.arguments}[/]")
            console.print(f"[dim]{step.content[:300]}…[/]")
        else:
            console.print(Markdown(step.content))

    settings = get_settings()
    if args.question:
        run_agent(args.question, settings, on_step=show)
        return
    console.print("Tryb interaktywny agenta (pusta linia kończy).")
    while question := console.input("[bold]> [/]").strip():
        run_agent(question, settings, on_step=show)


# Krótkie nagłówki, żeby tabela mieściła się w wąskim terminalu (legenda pod tabelą).
SHORT_HEADERS = {
    "mrr": "MRR",
    "ctx_chars": "ctx",
    "refusal_ok": "odm.",
    "false_refusal": "fałsz.odm.",
    "correct_refusal": "popr.odm.",
    "citation_hit": "cyt.",
    "cited_recall": "cyt.R",
    "inline_cited": "[n]",
    "count_ok": "licz.",
    "list_p": "L-P",
    "list_r": "L-R",
    "list_f1": "L-F1",
    "latency_s": "t[s]",
}
# Tabela per pytanie ma tylko podstawowe kolumny; reszta jest w tabelach grup i w pliku .md.
ROW_METRICS = ("mrr", "ctx_chars", "refusal_ok", "citation_hit", "inline_cited", "latency_s")
GROUP_METRICS = (
    "mrr", "refusal_ok", "false_refusal", "correct_refusal", "citation_hit", "cited_recall",
    "count_ok", "list_f1", "latency_s",
)  # fmt: skip


def _header(col: str) -> str:
    return escape(SHORT_HEADERS.get(col, col.replace("recall@", "R@")))


def print_group_table(title: str, groups: dict[str, dict]) -> None:
    """Średnie metryk osobno dla każdej grupy (kategorii albo trudności)."""
    from rag.eval import format_cell

    if not groups:
        return
    first = next(iter(groups.values()))
    recall_cols = [c for c in first if c.startswith("recall@")]
    cols = recall_cols[-1:] + [
        c for c in GROUP_METRICS if any(g.get(c) is not None for g in groups.values())
    ]
    table = Table(title=title)
    table.add_column("grupa", no_wrap=True)
    table.add_column("n", justify="right")
    for col in cols:
        table.add_column(_header(col), justify="right", no_wrap=True)
    for name, summary in groups.items():
        table.add_row(name, str(summary["questions"]), *(format_cell(summary.get(c)) for c in cols))
    console.print(table)


def cmd_eval(args: argparse.Namespace) -> None:
    from rag.eval import (
        DEFAULT_KS,
        evaluate,
        format_cell,
        load_golden,
        results_from_rows,
        save_results,
    )

    settings = get_settings()
    items = load_golden(Path(args.golden))
    if args.sample and args.sample < len(items):
        # równomiernie rozłożona próbka (zbiór jest uporządkowany od łatwych do trudnych)
        step = len(items) / args.sample
        items = [items[int(i * step)] for i in range(args.sample)]
        console.print(f"[yellow]Próbka: {len(items)} pytań co ~{step:.1f}-te (nie pełny pomiar).[/]")

    collected: list[dict] = []
    started = time.perf_counter()

    def progress(position: int, total: int, row: dict) -> None:
        collected.append(row)
        elapsed = time.perf_counter() - started
        eta = elapsed / position * (total - position)
        console.print(
            f"[dim]{position}/{total}[/] {row['id']} · pytanie {elapsed / position:.1f}s średnio · "
            f"minęło {elapsed / 60:.1f} min · zostało ~{eta / 60:.1f} min"
        )

    interrupted = False
    try:
        results = evaluate(
            items,
            settings,
            ks=DEFAULT_KS,
            generate=args.generate,
            collection=args.collection,
            on_row=progress,
        )
    except KeyboardInterrupt:
        if not collected:
            raise
        interrupted = True
        console.print(
            f"[yellow]Przerwano po {len(collected)}/{len(items)} pytaniach – "
            "zapisuję wyniki częściowe.[/]"
        )
        results = results_from_rows(collected, DEFAULT_KS)
    summary = results["summary"]
    # Kolumny = metryki policzone w tym przebiegu (bez --generate nie ma metryk odpowiedzi).
    metric_cols = [
        c
        for c in summary
        if c != "questions"
        and summary[c] is not None
        and (c.startswith("recall@") or c in ROW_METRICS)
    ]
    table = Table(
        title=f"Eval: {args.golden} · {settings.llm_model} · {args.collection or settings.collection}"
    )
    table.add_column("id", no_wrap=True, min_width=7)
    # Kolumna z pytaniem dostaje to, co zostaje po metrykach (pełne pytania są w pliku .md).
    question_width = max(10, min(50, console.width - 10 - 8 * len(metric_cols)))
    table.add_column("pytanie", width=question_width, no_wrap=True, overflow="ellipsis")
    for col in metric_cols:
        header = _header(col)
        # escape: „[n]” i „t[s]” rich wziąłby za znaczniki formatowania.
        table.add_column(header, justify="right", no_wrap=True, min_width=max(4, len(header)))
    for row in results["rows"]:
        table.add_row(row["id"], row["question"], *(format_cell(row.get(c)) for c in metric_cols))
    table.add_section()
    table.add_row(
        "ŚREDNIA",
        f"{summary['questions']} pytań",
        *(format_cell(summary.get(c)) for c in metric_cols),
    )
    console.print(table)
    print_group_table("Per kategoria", results.get("by_category", {}))
    print_group_table("Per trudność", results.get("by_difficulty", {}))
    legend = "R@k = recall@k · MRR = mean reciprocal rank · ctx = znaki kontekstu w top-k"
    if args.generate:
        legend += (
            " · odm. = poprawna odmowa/odpowiedź · fałsz.odm. = odmowa mimo odpowiedzi w dokumentach"
            " · popr.odm. = poprawna odmowa · cyt. = cytat z oczekiwanego źródła"
            " · cyt.R = część oczekiwanych źródeł zacytowana · [n] = znaczniki źródeł w tekście"
            " · licz. = oczekiwana liczba w odpowiedzi · L-P/R/F1 = numery scen na liście"
        )
    console.print(f"[dim]{escape(legend)}[/]")

    label = f"{Path(args.golden).stem}_{args.collection or settings.collection}"
    if args.generate:
        label += f"_{settings.llm_model.replace(':', '-').replace('/', '-')}"
    if args.sample:
        label += f"_probka{len(items)}"
    if interrupted:
        label += "_czesciowe"
    path = save_results(results, settings, Path("eval/results"), label)
    console.print(f"[dim]Zapisano: {path}[/]")
    if args.generate:
        console.print(
            f"[dim]Arkusz do oceny ręcznej: {path.with_name(path.stem + '_ocena.csv')} "
            "(uzupełnij `ocena`, potem: uv run rag grade <plik>)[/]"
        )


def cmd_rescore(args: argparse.Namespace) -> None:
    """Przelicza ocenę automatyczną (liczby, listy) na zapisanym przebiegu, bez pytania modelu."""
    import json

    from rag.eval import load_golden, rescore, to_markdown, write_grading_sheet

    path = Path(args.results)
    data = json.loads(path.read_text(encoding="utf-8"))
    new = rescore(data, load_golden(Path(args.golden)))
    stem = path.stem.removesuffix("_rescored")
    out = path.with_name(f"{stem}_rescored.json")
    out.write_text(
        json.dumps({"config": data.get("config", {}), **new}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    out.with_suffix(".md").write_text(to_markdown(new, out.stem), encoding="utf-8")
    sheet = out.with_name(f"{out.stem}_ocena.csv")
    write_grading_sheet(new, sheet)
    print_group_table("Per kategoria", new["by_category"])
    print_group_table("Per trudność", new["by_difficulty"])
    console.print(f"[dim]Zapisano: {out}\nNowy arkusz do oceny: {sheet}[/]")


def cmd_grade(args: argparse.Namespace) -> None:
    from rag.eval import format_cell
    from rag.grade import load_sheet, summarize_grades

    summary = summarize_grades(load_sheet(Path(args.sheet)), use_auto=args.use_auto)
    overall = summary["overall"]
    console.print(
        f"Ocenione: {overall['graded']}/{overall['n']} · poprawne: {overall['correct']} · "
        f"dokładność: {format_cell(overall['accuracy'])}"
    )
    if overall["graded"] < overall["n"]:
        console.print(
            f"[yellow]Bez oceny: {overall['n'] - overall['graded']} pytań "
            "(nie wchodzą do dokładności).[/]"
        )
    for title, key in (("Per kategoria", "by_category"), ("Per trudność", "by_difficulty")):
        table = Table(title=title)
        for header in ("grupa", "n", "ocenione", "poprawne", "dokładność"):
            table.add_column(header, justify="left" if header == "grupa" else "right")
        for name, s in summary[key].items():
            table.add_row(
                name, str(s["n"]), str(s["graded"]), str(s["correct"]), format_cell(s["accuracy"])
            )
        console.print(table)
    if summary["causes"] or summary["wrong_without_cause"]:
        console.print("[bold]Przyczyny błędów:[/]")
        for cause, count in sorted(summary["causes"].items(), key=lambda kv: -kv[1]):
            console.print(f"  {cause}: {count}")
        if summary["wrong_without_cause"]:
            console.print(f"  [yellow](bez przyczyny: {summary['wrong_without_cause']})[/]")
    console.print(f"[dim]Poprawne odmowy: {summary['correct_refusals']}[/]")


def cmd_check(_: argparse.Namespace) -> None:
    from rag.check import run_checks

    if not run_checks(get_settings()):
        sys.exit(1)


def cmd_reset_cache(_: argparse.Namespace) -> None:
    from rag.structured import CACHE_PATH, reset_cache

    if reset_cache():
        console.print(f"Usunięto {CACHE_PATH} – tryb structured output zostanie wykryty od nowa.")
    else:
        console.print(f"Brak cache ({CACHE_PATH}).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rag", description="Starter RAG – kurs IDM (WEiTI PW)")
    parser.add_argument("-v", "--verbose", action="store_true", help="więcej logów")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="zaindeksuj dokumenty")
    p.add_argument("paths", nargs="*", help="pliki lub katalogi (domyślnie DATA_DIR z .env)")
    p.add_argument("--chunking", choices=CHUNKING_STRATEGIES, help="strategia chunkingu")
    p.add_argument("--collection", help="nazwa kolekcji (domyślnie COLLECTION z .env)")
    p.add_argument("--recreate", action="store_true", help="usuń i utwórz kolekcję od nowa")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("ask", help="zadaj pytanie (RAG)")
    p.add_argument("question")
    p.add_argument("-k", type=int, help="liczba chunków w kontekście (domyślnie TOP_K)")
    p.add_argument("--collection")
    p.add_argument("--show-context", action="store_true", help="pokaż pełny tekst chunków")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("agent", help="agent z narzędziem search_docs")
    p.add_argument("question", nargs="?")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("eval", help="ewaluacja na złotym zbiorze")
    p.add_argument("--golden", default="eval/golden_sample.jsonl")
    p.add_argument("--generate", action="store_true", help="oceniaj też odpowiedzi (wolniej)")
    p.add_argument("--sample", type=int, help="tylko N pytań (równomiernie), do szybkiej próby")
    p.add_argument("--collection")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("rescore", help="przelicz ocenę automatyczną na zapisanym przebiegu (.json)")
    p.add_argument("results", help="plik eval/results/*.json z przebiegu z --generate")
    p.add_argument("--golden", default="eval/pytania.jsonl")
    p.set_defaults(func=cmd_rescore)

    p = sub.add_parser("grade", help="podsumuj ręczną ocenę z arkusza CSV (eval/results/*_ocena.csv)")
    p.add_argument("sheet", help="arkusz CSV z kolumną `ocena`")
    p.add_argument("--use-auto", action="store_true", help="uzupełnij braki podpowiedzią ocena_auto")
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("check", help="smoke test środowiska")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("reset-cache", help="usuń cache wykrytego trybu structured output")
    p.set_defaults(func=cmd_reset_cache)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(show_path=False, show_time=False)],
    )
    # Wykrycie trybu structured output pokazujemy zawsze (jedna linia na INFO) – to ważna
    # informacja dydaktyczna. Szczegóły każdej próby: `rag -v ...` (DEBUG).
    logging.getLogger("rag.structured").setLevel(logging.DEBUG if args.verbose else logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        args.func(args)
    except (RuntimeError, ValueError) as err:
        console.print(f"[red]Błąd:[/] {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
