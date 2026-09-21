"""Idempotent filing downloader.

Layout: ``data/raw/edgar/<cik>/<accession>/<primary_doc>`` plus ``manifest.json`` holding the
source URL, sha256, size and fetch time. A filing is skipped when its manifest exists and the
file on disk still matches the recorded hash, so re-running ingestion is a cheap no-op, while a
truncated or corrupted file is detected and re-fetched.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from finsight.core.logging import get_logger
from finsight.core.schemas import FilingRef

log = get_logger(__name__)


class DocumentSource(Protocol):
    def get_document(self, cik: str, accession: str, primary_doc: str) -> bytes: ...


@dataclass(frozen=True)
class DownloadResult:
    ref: FilingRef
    path: Path
    sha256: str
    size_bytes: int
    status: Literal["downloaded", "skipped"]


def filing_dir(raw_dir: Path, ref: FilingRef) -> Path:
    return raw_dir / "edgar" / ref.cik / ref.accession


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download_filing(
    source: DocumentSource, ref: FilingRef, raw_dir: Path, *, force: bool = False
) -> DownloadResult:
    folder = filing_dir(raw_dir, ref)
    doc_path = folder / ref.primary_doc
    manifest_path = folder / "manifest.json"

    if not force and manifest_path.is_file() and doc_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("sha256") == _sha256(doc_path):
                return DownloadResult(
                    ref, doc_path, manifest["sha256"], int(manifest["size_bytes"]), "skipped"
                )
            log.warning("edgar.filing_hash_mismatch_refetching", accession=ref.accession)
        except (json.JSONDecodeError, KeyError):
            log.warning("edgar.bad_manifest_refetching", accession=ref.accession)

    body = source.get_document(ref.cik, ref.accession, ref.primary_doc)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = doc_path.with_suffix(doc_path.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(doc_path)
    digest = hashlib.sha256(body).hexdigest()
    manifest_path.write_text(
        json.dumps(
            {
                "ticker": ref.ticker,
                "form": ref.form.value,
                "fiscal_year": ref.fiscal_year,
                "accession": ref.accession,
                "url": ref.url,
                "primary_doc": ref.primary_doc,
                "sha256": digest,
                "size_bytes": len(body),
                "fetched_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return DownloadResult(ref, doc_path, digest, len(body), "downloaded")
