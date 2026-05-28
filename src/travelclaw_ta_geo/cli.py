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
    max_images_per_geo: int = typer.Option(100, "--max-images-per-geo", min=0, max=10000),
    output_dir: Path = typer.Option(Path("data/poc"), "--output-dir"),
    upload: bool = typer.Option(False, "--upload/--no-upload"),
) -> None:
    """Run a small crawl for structure validation."""
    _run_crawl(seed, limit_geos, max_images_per_geo, output_dir, upload)


@app.command()
def crawl(
    seed: Path = typer.Option(Path("seeds/destinations.sample.csv"), "--seed", exists=True, readable=True),
    output_dir: Path = typer.Option(Path("data/runs"), "--output-dir"),
    max_images_per_geo: int = typer.Option(10000, "--max-images-per-geo", min=0, max=10000),
    limit_geos: int | None = typer.Option(None, "--limit-geos", min=1),
    upload: bool = typer.Option(False, "--upload/--no-upload"),
) -> None:
    """Run a full Tripadvisor geo crawl."""
    _run_crawl(seed, limit_geos, max_images_per_geo, output_dir, upload)


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


if __name__ == "__main__":
    app()

