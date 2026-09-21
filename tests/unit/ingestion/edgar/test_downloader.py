"""Downloader: idempotence, integrity checking and crash safety."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from finsight.core.schemas import FilingRef, FiscalPeriod, FormType
from finsight.ingestion.edgar.downloader import download_filing, filing_dir

pytestmark = pytest.mark.unit

REF = FilingRef(
    cik="0000320193",
    ticker="AAPL",
    company="Apple Inc.",
    form=FormType.TEN_K,
    accession="0000320193-24-000123",
    filed=date(2024, 11, 1),
    period_of_report=date(2024, 9, 28),
    fiscal_year=2024,
    fiscal_period=FiscalPeriod.FY,
    primary_doc="aapl-20240928.htm",
    url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
)


class FakeSource:
    def __init__(self, body: bytes = b"<html>10-K</html>") -> None:
        self.body, self.calls = body, 0

    def get_document(self, cik: str, accession: str, primary_doc: str) -> bytes:
        self.calls += 1
        return self.body


def test_first_download_writes_file_and_manifest(tmp_path: Path) -> None:
    src = FakeSource()
    result = download_filing(src, REF, tmp_path)
    assert result.status == "downloaded"
    assert result.path == filing_dir(tmp_path, REF) / "aapl-20240928.htm"
    assert result.path.read_bytes() == b"<html>10-K</html>"
    manifest = json.loads((filing_dir(tmp_path, REF) / "manifest.json").read_text())
    assert manifest["sha256"] == hashlib.sha256(b"<html>10-K</html>").hexdigest()
    assert manifest["size_bytes"] == len(b"<html>10-K</html>")
    assert manifest["accession"] == REF.accession
    assert manifest["url"] == REF.url


def test_second_run_is_a_noop(tmp_path: Path) -> None:
    src = FakeSource()
    download_filing(src, REF, tmp_path)
    again = download_filing(src, REF, tmp_path)
    assert again.status == "skipped"
    assert src.calls == 1


def test_corrupted_file_is_detected_and_refetched(tmp_path: Path) -> None:
    src = FakeSource()
    first = download_filing(src, REF, tmp_path)
    first.path.write_bytes(b"truncated")  # simulate a partial / bit-rotted file
    again = download_filing(src, REF, tmp_path)
    assert again.status == "downloaded"
    assert again.path.read_bytes() == b"<html>10-K</html>"
    assert src.calls == 2


def test_force_redownloads(tmp_path: Path) -> None:
    src = FakeSource()
    download_filing(src, REF, tmp_path)
    assert download_filing(src, REF, tmp_path, force=True).status == "downloaded"
    assert src.calls == 2


def test_corrupt_manifest_triggers_refetch(tmp_path: Path) -> None:
    src = FakeSource()
    download_filing(src, REF, tmp_path)
    (filing_dir(tmp_path, REF) / "manifest.json").write_text("{not json")
    assert download_filing(src, REF, tmp_path).status == "downloaded"


def test_no_partial_files_are_left_behind(tmp_path: Path) -> None:
    download_filing(FakeSource(), REF, tmp_path)
    assert not list(tmp_path.rglob("*.part"))
