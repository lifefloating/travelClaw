from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from travelclaw_ta_geo.crawler import TripadvisorGeoCrawler
from travelclaw_ta_geo.output.package import PackageBuilder
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.storage.r2 import R2Uploader

app = typer.Typer(help="Tripadvisor geo crawler and delivery packager.")
console = Console()


@app.command()
def poc(
    seed: Path = typer.Option(Path("seeds/destinations.sample.csv"), "--seed", exists=True, readable=True),
    limit_geos: int = typer.Option(3, "--limit-geos", min=1),
    max_images_per_geo: int = typer.Option(100, "--max-images-per-geo", min=0),
    output_dir: Path = typer.Option(Path("data/poc"), "--output-dir"),
    upload: bool = typer.Option(False, "--upload/--no-upload"),
) -> None:
    """Run a small crawl for structure validation."""
    _run_crawl(seed, limit_geos, max_images_per_geo, output_dir, upload)


@app.command()
def crawl(
    seed: Path = typer.Option(Path("seeds/destinations.sample.csv"), "--seed", exists=True, readable=True),
    output_dir: Path = typer.Option(Path("data/runs"), "--output-dir"),
    max_images_per_geo: int | None = typer.Option(None, "--max-images-per-geo", min=0),
    limit_geos: int | None = typer.Option(None, "--limit-geos", min=1),
    upload: bool = typer.Option(False, "--upload/--no-upload"),
) -> None:
    """Run a full Tripadvisor geo crawl."""
    settings = Settings()
    images = max_images_per_geo if max_images_per_geo is not None else settings.ta_max_images_per_geo
    _run_crawl(seed, limit_geos, images, output_dir, upload)


@app.command("package")
def package_command(
    run_dir: Path = typer.Option(..., "--run-dir", exists=True, file_okay=False, readable=True),
    package_dir: Path | None = typer.Option(None, "--package-dir"),
) -> None:
    """Build a package from an existing run directory."""
    settings = Settings()
    result = PackageBuilder(settings).build(run_dir, package_dir)
    console.print(f"package_dir={result.package_dir}")
    console.print(f"manifest={result.manifest_path}")


@app.command()
def upload(
    package_dir: Path = typer.Option(..., "--package-dir", exists=True, file_okay=False, readable=True),
) -> None:
    """Upload an already-built package to R2 when R2 config is enabled and complete."""
    settings = Settings()
    uploader = R2Uploader(settings)
    if not uploader.enabled(upload_requested=True):
        console.print("R2 upload skipped: set R2_UPLOAD_ENABLED=true and complete R2 config in .env.")
        raise typer.Exit(code=0)
    timestamp = _timestamp_from_manifest(package_dir)
    result = uploader.upload_package(package_dir, timestamp=timestamp)
    console.print(f"uploaded_files={len(result.uploaded_keys)}")
    console.print(f"ready_key={result.ready_key}")


def _run_crawl(
    seed: Path,
    limit_geos: int | None,
    max_images_per_geo: int,
    output_dir: Path,
    upload_requested: bool,
) -> None:
    settings = Settings()
    console.print(
        f"crawl seed={seed} limit_geos={limit_geos or 'all'} max_images_per_geo={max_images_per_geo} "
        f"upload_requested={upload_requested}"
    )
    result = TripadvisorGeoCrawler(settings).crawl(
        seed_path=seed,
        output_dir=output_dir,
        limit_geos=limit_geos,
        max_images_per_geo=max_images_per_geo,
        upload_requested=upload_requested,
    )
    console.print(f"run_dir={result.run_dir}")
    console.print(f"package_dir={result.package.package_dir}")
    console.print(f"geo_rows={result.geo_rows} media_rows={result.media_rows} error_rows={result.error_rows}")
    if result.upload:
        console.print(f"ready_key={result.upload.ready_key}")
    elif upload_requested:
        console.print("R2 upload skipped: R2_UPLOAD_ENABLED must be true and R2 config must be complete.")


def _timestamp_from_manifest(package_dir: Path) -> str:
    manifest_path = package_dir / "manifest.json"
    if not manifest_path.exists():
        return package_dir.name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    delivered_at = manifest.get("delivered_at")
    if not delivered_at:
        return package_dir.name
    parsed = datetime.strptime(delivered_at, "%Y-%m-%dT%H:%M:%SZ")
    return parsed.strftime("%Y-%m-%dT%H%M%SZ")


@app.command()
def run(
    seed: Path = typer.Option(Path("seeds/destinations.sample.csv"), "--seed", exists=True, readable=True),
    cities: str | None = typer.Option(None, "--cities", help="Comma-separated geo_id or name; omit with --all for everything."),
    all_cities: bool = typer.Option(False, "--all", help="Process every seed."),
    limit_geos: int | None = typer.Option(None, "--limit-geos", min=1),
    max_images_per_geo: int | None = typer.Option(None, "--max-images-per-geo", min=0),
    parallel: int = typer.Option(1, "--parallel", min=1, help="Worker processes (each has its own browser profile)."),
    upload: bool = typer.Option(False, "--upload/--no-upload"),
    force: bool = typer.Option(False, "--force", help="Re-crawl cities already marked done in persistent state."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip upload and disk cleanup; useful for small validation runs."),
) -> None:
    """Per-city crawl: crawl -> package -> upload -> delete images, with persistent skip."""
    from travelclaw_ta_geo import orchestrator
    from travelclaw_ta_geo.city_runner import CityOptions
    from travelclaw_ta_geo.orchestrator import SelectOptions

    settings = Settings()
    images = max_images_per_geo if max_images_per_geo is not None else settings.ta_max_images_per_geo
    select = SelectOptions(
        seed_path=seed,
        cities=cities.split(",") if cities else None,
        all_cities=all_cities,
        limit_geos=limit_geos,
    )
    options = CityOptions(max_images_per_geo=images, upload=upload, force=force, dry_run=dry_run)
    console.print(
        f"run cities={cities or ('all' if all_cities else 'seed-order')} parallel={parallel} "
        f"max_images_per_geo={images} upload={upload} dry_run={dry_run} data_root={settings.data_root}"
    )
    outcomes = orchestrator.run(settings, select, options, parallel=parallel)
    if not outcomes:
        console.print("no cities selected; check --cities/--all/--seed")
        raise typer.Exit(code=1)
    summary = orchestrator.summarize(outcomes)
    console.print(f"summary={summary}")
    for outcome in outcomes:
        if outcome.stage == "failed":
            console.print(f"[red]FAILED[/] {outcome.city_key}: {outcome.message}")


@app.command()
def city(
    geo: str = typer.Argument(..., help="geo_id or name to match in the seed file."),
    seed: Path = typer.Option(Path("seeds/destinations.sample.csv"), "--seed", exists=True, readable=True),
    max_images_per_geo: int = typer.Option(200, "--max-images-per-geo", min=0),
    upload: bool = typer.Option(False, "--upload/--no-upload"),
    force: bool = typer.Option(False, "--force"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run a single city end-to-end (debugging convenience, in-process)."""
    from travelclaw_ta_geo.city_runner import CityOptions, run_city
    from travelclaw_ta_geo.orchestrator import SelectOptions, select_seeds

    settings = Settings()
    seeds = select_seeds(SelectOptions(seed_path=seed, cities=[geo]))
    if not seeds:
        console.print(f"no seed matched {geo!r}")
        raise typer.Exit(code=1)
    options = CityOptions(max_images_per_geo=max_images_per_geo, upload=upload, force=force, dry_run=dry_run)
    outcome = run_city(settings, seeds[0], options)
    console.print(
        f"city={outcome.city_key} stage={outcome.stage} geo_rows={outcome.geo_rows} "
        f"media_rows={outcome.media_rows} error_rows={outcome.error_rows} r2_ts={outcome.r2_timestamp}"
    )


@app.command()
def monitor(
    once: bool = typer.Option(False, "--once", help="Print a single snapshot instead of a live view."),
    interval: float = typer.Option(2.0, "--interval", min=0.5),
) -> None:
    """Live dashboard of per-city crawl progress (reads <data_root>/status)."""
    from travelclaw_ta_geo import monitor as monitor_mod

    settings = Settings()
    layout = settings.layout
    if once:
        monitor_mod.snapshot(layout, console=console)
    else:
        try:
            monitor_mod.watch(layout, interval=interval, console=console)
        except KeyboardInterrupt:
            console.print("monitor stopped")


@app.command()
def preheat(
    interactive: bool = typer.Option(False, "--interactive", help="Wait for Enter (local, visible window)."),
    url: str | None = typer.Option(None, "--url", help="Override the warm-up URL."),
    settle_seconds: float = typer.Option(8.0, "--settle-seconds", min=0),
) -> None:
    """Warm the base browser profile so cf_clearance is captured for static requests."""
    from travelclaw_ta_geo.preheat import DEFAULT_WARM_URL
    from travelclaw_ta_geo.preheat import preheat as run_preheat

    settings = Settings()
    status = run_preheat(
        settings,
        url=url or DEFAULT_WARM_URL,
        interactive=interactive,
        settle_seconds=settle_seconds,
    )
    if status and status < 400:
        console.print(f"[green]preheat ok[/] HTTP {status}")
    else:
        console.print(f"[yellow]preheat finished with HTTP {status}[/] — inspect the profile before full crawl")


if __name__ == "__main__":
    app()

