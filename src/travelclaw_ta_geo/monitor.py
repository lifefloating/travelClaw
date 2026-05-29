from __future__ import annotations

import time

from rich.console import Console
from rich.live import Live
from rich.table import Table

from travelclaw_ta_geo.paths import DataLayout
from travelclaw_ta_geo.progress import CityStatus, Stage, read_all_statuses

_ACTIVE_STAGES = {
    Stage.DISCOVERING.value,
    Stage.FETCHING_DETAIL.value,
    Stage.GALLERY.value,
    Stage.DOWNLOADING.value,
    Stage.PACKAGING.value,
    Stage.UPLOADING.value,
    Stage.CLEANUP.value,
}

_STAGE_STYLE = {
    Stage.QUEUED.value: "dim",
    Stage.DONE.value: "green",
    Stage.FAILED.value: "red",
    Stage.SKIPPED.value: "yellow",
    Stage.UPLOADING.value: "cyan",
    Stage.DOWNLOADING.value: "blue",
}


def _progress_bar(done: int, total: int, width: int = 18) -> str:
    if total <= 0:
        return ""
    ratio = min(1.0, done / total)
    filled = int(ratio * width)
    return f"[{'#' * filled}{'.' * (width - filled)}] {done}/{total}"


def _render(statuses: list[CityStatus]) -> Table:
    table = Table(title="Tripadvisor Geo Crawl — 进度监控", expand=True)
    table.add_column("City", no_wrap=True)
    table.add_column("geo_id", justify="right")
    table.add_column("Stage", no_wrap=True)
    table.add_column("W", justify="right")
    table.add_column("Images")
    table.add_column("geo/media/err", justify="right")
    table.add_column("R2 ts", no_wrap=True)
    table.add_column("Msg", overflow="fold")

    counts: dict[str, int] = {}
    for s in statuses:
        counts[s.stage] = counts.get(s.stage, 0) + 1
        stage_label = f"[{_STAGE_STYLE.get(s.stage, 'white')}]{s.stage}[/]"
        images = _progress_bar(s.images_done, s.images_total) if s.stage in _ACTIVE_STAGES or s.images_total else ""
        table.add_row(
            s.name or s.city_key,
            str(s.geo_id or ""),
            stage_label,
            str(s.worker if s.worker is not None else ""),
            images,
            f"{s.geo_rows}/{s.media_rows}/{s.error_rows}",
            s.r2_timestamp,
            (s.message or "")[:60],
        )

    done = counts.get(Stage.DONE.value, 0)
    failed = counts.get(Stage.FAILED.value, 0)
    skipped = counts.get(Stage.SKIPPED.value, 0)
    active = sum(v for k, v in counts.items() if k in _ACTIVE_STAGES)
    queued = counts.get(Stage.QUEUED.value, 0)
    table.caption = (
        f"total={len(statuses)}  done={done}  failed={failed}  "
        f"skipped={skipped}  active={active}  queued={queued}"
    )
    return table


def snapshot(layout: DataLayout, console: Console | None = None) -> None:
    console = console or Console()
    statuses = read_all_statuses(layout.status)
    if not statuses:
        console.print(f"no status files under {layout.status}")
        return
    console.print(_render(statuses))


def watch(layout: DataLayout, interval: float = 2.0, console: Console | None = None) -> None:
    console = console or Console()
    status_dir = layout.status
    with Live(_render(read_all_statuses(status_dir)), console=console, refresh_per_second=4) as live:
        while True:
            statuses = read_all_statuses(status_dir)
            live.update(_render(statuses))
            if statuses and all(s.is_terminal for s in statuses):
                # One final paint, then stop so the command exits cleanly.
                time.sleep(interval)
                live.update(_render(read_all_statuses(status_dir)))
                break
            time.sleep(interval)
