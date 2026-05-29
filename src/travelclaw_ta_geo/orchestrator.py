from __future__ import annotations

import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from travelclaw_ta_geo.city_runner import CityOptions, CityOutcome, run_city
from travelclaw_ta_geo.paths import DataLayout
from travelclaw_ta_geo.progress import CityStatus, Stage, StatusWriter
from travelclaw_ta_geo.seeds import DestinationSeed, load_seeds
from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.state import PersistentState


@dataclass(frozen=True)
class SelectOptions:
    seed_path: Path
    cities: list[str] | None = None  # geo_id (with/without leading g) or name match
    all_cities: bool = False
    limit_geos: int | None = None


def select_seeds(opts: SelectOptions) -> list[DestinationSeed]:
    seeds = load_seeds(opts.seed_path)
    if opts.cities:
        wanted = {c.strip().lower().lstrip("g") for c in opts.cities if c.strip()}
        selected = [s for s in seeds if _seed_matches(s, wanted)]
    else:
        selected = list(seeds)
    if opts.limit_geos is not None and opts.limit_geos > 0:
        selected = selected[: opts.limit_geos]
    return selected


def _seed_matches(seed: DestinationSeed, wanted: set[str]) -> bool:
    candidates = {
        str(seed.tripadvisor_geo_id).lstrip("g").lower(),
        seed.key.lower(),
        (seed.name_en or "").lower(),
        (seed.name_cn or "").lower(),
    }
    return bool(candidates & wanted)


def _ensure_worker_profile(layout: DataLayout, worker_index: int) -> None:
    """Give each worker its own browser profile copied from the warmed base so
    parallel StealthySession opens never collide on one user_data_dir."""
    worker_dir = layout.worker_profile(worker_index)
    if worker_dir.exists() and any(worker_dir.iterdir()):
        return
    base = layout.browser_base
    if base.exists() and any(base.iterdir()):
        worker_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(base, worker_dir, dirs_exist_ok=True)
    else:
        worker_dir.mkdir(parents=True, exist_ok=True)


def _run_slice(
    base_settings: Settings,
    worker_index: int,
    seeds: list[DestinationSeed],
    options: CityOptions,
) -> list[CityOutcome]:
    """Runs in a worker process: set up the worker profile, then process its
    assigned cities serially (one browser/session reused across them)."""
    layout = base_settings.layout
    layout.ensure_base_dirs()
    _ensure_worker_profile(layout, worker_index)
    settings = base_settings.for_worker(worker_index)
    persistent = None if options.dry_run else PersistentState(layout.state_db)
    outcomes: list[CityOutcome] = []
    try:
        for seed in seeds:
            opts = CityOptions(
                max_images_per_geo=options.max_images_per_geo,
                upload=options.upload,
                force=options.force,
                dry_run=options.dry_run,
                worker_index=worker_index,
            )
            outcomes.append(run_city(settings, seed, opts, layout=layout, persistent=persistent))
    finally:
        if persistent is not None:
            persistent.close()
    return outcomes


def run(
    settings: Settings,
    select: SelectOptions,
    options: CityOptions,
    parallel: int = 1,
) -> list[CityOutcome]:
    layout = settings.layout
    layout.ensure_base_dirs()
    seeds = select_seeds(select)
    if not seeds:
        return []

    # Pre-seed queued status for every selected city so the monitor shows the
    # full work list immediately, even before a worker picks each one up.
    for seed in seeds:
        path = layout.city_status(seed.key)
        StatusWriter(path, CityStatus(city_key=seed.key, name=seed.name_en or seed.name_cn))

    parallel = max(1, min(parallel, settings.ta_worker_count, len(seeds)))

    if parallel == 1:
        return _run_slice(settings, 0, seeds, options)

    # Round-robin assignment keeps per-worker load balanced.
    slices: list[list[DestinationSeed]] = [[] for _ in range(parallel)]
    for idx, seed in enumerate(seeds):
        slices[idx % parallel].append(seed)

    outcomes: list[CityOutcome] = []
    with ProcessPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(_run_slice, settings, worker_index, slice_seeds, options): worker_index
            for worker_index, slice_seeds in enumerate(slices)
            if slice_seeds
        }
        for future in as_completed(futures):
            outcomes.extend(future.result())
    return outcomes


def summarize(outcomes: list[CityOutcome]) -> dict[str, int]:
    summary = {s.value: 0 for s in Stage}
    for outcome in outcomes:
        summary[outcome.stage] = summary.get(outcome.stage, 0) + 1
    return {k: v for k, v in summary.items() if v}
