from __future__ import annotations

import re
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from imagededup.methods import PHash
from PIL import Image

from travelclaw_ta_geo.settings import Settings
from travelclaw_ta_geo.tripadvisor.http import TripadvisorHttpClient
from travelclaw_ta_geo.tripadvisor.models import MediaCandidate
from travelclaw_ta_geo.tripadvisor.parsing import mime_to_extension

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
PIL_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


@dataclass(frozen=True)
class DownloadedMedia:
    line: dict
    path: Path


@dataclass(frozen=True)
class MediaDownloadError:
    candidate: MediaCandidate
    message: str


@dataclass(frozen=True)
class _DownloadOutcome:
    media: DownloadedMedia
    phash: str


class MediaDownloader:
    def __init__(self, settings: Settings, client: TripadvisorHttpClient) -> None:
        self.settings = settings
        self.client = client
        self._phasher = PHash()

    def download_many(
        self,
        candidates: Iterable[MediaCandidate],
        *,
        run_dir: Path,
        geo_id: int,
        geo_record_id: str,
        captured_at: str,
        max_items: int,
    ) -> Iterable[DownloadedMedia | MediaDownloadError]:
        limited = list(candidates)[:max_items]
        media_dir = run_dir / "media" / str(geo_id)
        media_dir.mkdir(parents=True, exist_ok=True)
        kept_hashes: list[str] = []
        threshold = self.settings.ta_image_dedupe_distance
        with ThreadPoolExecutor(max_workers=self.settings.ta_image_concurrency) as pool:
            futures = {
                pool.submit(
                    self._download_one,
                    candidate,
                    index,
                    media_dir,
                    geo_id,
                    geo_record_id,
                    captured_at,
                    run_dir,
                ): candidate
                for index, candidate in enumerate(limited, start=1)
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    outcome = future.result()
                except Exception as exc:
                    yield MediaDownloadError(candidate=candidate, message=str(exc))
                    continue
                duplicate_of = self._find_duplicate(outcome.phash, kept_hashes, threshold)
                if duplicate_of is not None:
                    try:
                        outcome.media.path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    yield MediaDownloadError(
                        candidate=candidate,
                        message=f"duplicate of phash#{duplicate_of} (distance<={threshold})",
                    )
                    continue
                kept_hashes.append(outcome.phash)
                yield outcome.media

    def _download_one(
        self,
        candidate: MediaCandidate,
        index: int,
        media_dir: Path,
        geo_id: int,
        geo_record_id: str,
        captured_at: str,
        run_dir: Path,
    ) -> _DownloadOutcome:
        data, content_type = self.client.download_bytes(candidate.url)
        if len(data) > self.settings.ta_image_max_bytes:
            raise ValueError(f"image exceeds max bytes: {len(data)}")
        width, height, mime_type, phash = self._inspect_image(data, content_type)
        if mime_type not in ALLOWED_MIME_TYPES:
            raise ValueError(f"unsupported image mime type: {mime_type}")
        if max(width, height) > self.settings.ta_image_max_side:
            raise ValueError(f"image exceeds max side: {width}x{height}")

        extension = mime_to_extension(mime_type)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate.media_id)[:80] or f"{index:06d}"
        file_name = f"{index:06d}_{safe_id}{extension}"
        path = media_dir / file_name
        path.write_bytes(data)
        relative = path.relative_to(run_dir).as_posix()
        line = {
            "record_id": f"qiqi:tripadvisor:media:{geo_id}:{candidate.media_id}",
            "source": "tripadvisor",
            "source_id": candidate.media_id,
            "source_url": candidate.source_url,
            "captured_at": captured_at,
            "path": relative,
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "phash": phash,
            "caption": candidate.caption,
            "license": self.settings.license,
            "attribution": self.settings.attribution,
            "depicts": {"record_id": geo_record_id},
            "raw": {"source_kind": candidate.source_kind},
        }
        return _DownloadOutcome(media=DownloadedMedia(line=line, path=path), phash=phash)

    def _inspect_image(self, data: bytes, content_type: str) -> tuple[int, int, str, str]:
        with Image.open(BytesIO(data)) as image:
            image.load()
            width, height = image.size
            mime_type = PIL_MIME.get((image.format or "").upper(), content_type.split(";")[0].strip().lower())
            phash = self._phash_from_pil(image)
        return width, height, mime_type, phash

    def _phash_from_pil(self, image: Image.Image) -> str:
        rgb = image.convert("RGB")
        # imagededup's encode_image takes either a file path or an HxWx3 uint8 ndarray
        return self._phasher.encode_image(image_array=np.asarray(rgb, dtype=np.uint8))

    @staticmethod
    def _find_duplicate(phash: str, kept: list[str], threshold: int) -> int | None:
        for idx, existing in enumerate(kept):
            if _hamming(existing, phash) <= threshold:
                return idx
        return None


def _hamming(a: str, b: str) -> int:
    # PHash returns equal-length hex strings; convert to int once and xor.
    if len(a) != len(b):
        return max(len(a), len(b))
    return bin(int(a, 16) ^ int(b, 16)).count("1")
