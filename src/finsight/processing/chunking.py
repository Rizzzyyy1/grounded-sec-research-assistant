"""Section-aware chunker.

Design (docs/DESIGN.md §7.1):

* runs **per section**, so a chunk can never cross an Item boundary;
* token-budgeted (default 400) with sentence-granular overlap (default 15%);
* paragraph boundaries are respected; an oversized paragraph is split on sentences, an oversized
  sentence on words - so no chunk ever exceeds the budget;
* tables are **standalone chunks** with their caption; a table larger than twice the budget is
  split by rows with the header row repeated, so every piece stays interpretable;
* ids are deterministic (``Chunk.make_id``), making re-indexing idempotent.

Token counts use a fast regex approximation (words + punctuation), close to BPE counts for
English prose and numbers, deterministic and dependency-free - so tests and CI never download a
tokenizer vocabulary.
"""

from __future__ import annotations

import re

from finsight.config.settings import ChunkingSettings
from finsight.core.schemas import Chunk, ChunkMetadata, ChunkType, FilingRef
from finsight.processing.enrichment import add_context_header
from finsight.processing.html_parser import Block
from finsight.processing.sections import SectionBlocks

_TOKEN = re.compile(r"\w+|[^\w\s]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“(])")
_CAPTION_MAX_TOKENS = 40


def count_tokens(text: str) -> int:
    return len(_TOKEN.findall(text))


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s]


def _hard_split(text: str, budget: int) -> list[str]:
    """Last resort for a single 'sentence' longer than the budget: split on words."""
    words = text.split()
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for word in words:
        w = count_tokens(word)
        if current and size + w > budget:
            parts.append(" ".join(current))
            current, size = [], 0
        current.append(word)
        size += w
    if current:
        parts.append(" ".join(current))
    return parts


def _fit_units(text: str, budget: int) -> list[str]:
    """Split one paragraph into pieces that each fit the budget."""
    if count_tokens(text) <= budget:
        return [text]
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in split_sentences(text):
        s = count_tokens(sentence)
        if s > budget:
            if current:
                pieces.append(" ".join(current))
                current, size = [], 0
            pieces.extend(_hard_split(sentence, budget))
            continue
        if current and size + s > budget:
            pieces.append(" ".join(current))
            current, size = [], 0
        current.append(sentence)
        size += s
    if current:
        pieces.append(" ".join(current))
    return pieces


def _tail(text: str, budget: int) -> str:
    """The last sentences of ``text`` totalling at most ``budget`` tokens (overlap material)."""
    if budget <= 0:
        return ""
    taken: list[str] = []
    size = 0
    for sentence in reversed(split_sentences(text)):
        s = count_tokens(sentence)
        if size + s > budget:
            break
        taken.append(sentence)
        size += s
    return " ".join(reversed(taken))


def _row_line(row: tuple[str, ...]) -> str:
    return f"| {' | '.join(row)} |" if len(row) > 1 else row[0]


def _table_pieces(block: Block, caption: str, budget: int) -> list[str]:
    """Render a table as one or more chunk texts (header row repeated when split)."""
    lines = [_row_line(r) for r in block.rows]
    head = f"{caption}\n" if caption else ""
    whole = head + "\n".join(lines)
    if count_tokens(whole) <= 2 * budget:
        return [whole]
    header = lines[0]
    pieces: list[str] = []
    current: list[str] = []
    size = count_tokens(head) + count_tokens(header)
    for line in lines[1:]:
        t = count_tokens(line)
        if current and size + t > budget:
            pieces.append(head + "\n".join([header, *current]))
            current, size = [], count_tokens(head) + count_tokens(header)
        current.append(line)
        size += t
    if current:
        pieces.append(head + "\n".join([header, *current]))
    return pieces


def _absorb_slivers(
    raw: list[tuple[ChunkType, str]], min_tokens: int, budget: int
) -> list[tuple[ChunkType, str]]:
    """Give every text fragment below ``min_tokens`` a home instead of its own useless chunk.

    A fragment joins the previous text chunk when that stays near the budget; otherwise it is
    prepended to the *next* chunk (typically the table it introduces). A trailing fragment joins
    whatever precedes it. Text chunks may therefore exceed ``budget`` by a few ``min_tokens``.
    """
    out: list[tuple[ChunkType, str]] = []
    pending = ""
    for kind, text in raw:
        if kind is ChunkType.TEXT and count_tokens(text) < min_tokens:
            if (
                out
                and out[-1][0] is ChunkType.TEXT
                and (count_tokens(out[-1][1]) + count_tokens(text) <= budget + min_tokens)
            ):
                out[-1] = (ChunkType.TEXT, f"{out[-1][1]}\n\n{text}")
            else:
                pending = f"{pending}\n\n{text}" if pending else text
            continue
        body = f"{pending}\n{text}" if pending else text
        pending = ""
        out.append((kind, body))
    if pending:
        if out:
            out[-1] = (out[-1][0], f"{out[-1][1]}\n{pending}")
        else:
            out.append((ChunkType.TEXT, pending))
    return out


def chunk_section(section: SectionBlocks, filing: FilingRef, cfg: ChunkingSettings) -> list[Chunk]:
    budget = cfg.target_tokens
    overlap_budget = int(budget * cfg.overlap_ratio)
    raw: list[tuple[ChunkType, str]] = []

    buffer: list[str] = []
    size = 0

    def flush(*, keep_overlap: bool) -> None:
        nonlocal buffer, size
        if not buffer:
            return
        text = "\n\n".join(buffer)
        raw.append((ChunkType.TEXT, text))
        tail = _tail(buffer[-1], overlap_budget) if keep_overlap else ""
        buffer, size = ([tail], count_tokens(tail)) if tail else ([], 0)

    for block in section.blocks:
        if block.is_table and cfg.tables_as_chunks:
            # A short paragraph right before a table is its caption ("(in millions)", a title):
            # move it onto the table instead of leaving a stranded fragment behind.
            caption = ""
            if buffer and count_tokens(buffer[-1]) <= _CAPTION_MAX_TOKENS:
                caption = buffer.pop()
                size = sum(count_tokens(u) for u in buffer)
            flush(keep_overlap=False)
            buffer, size = [], 0
            raw.extend((ChunkType.TABLE, p) for p in _table_pieces(block, caption, budget))
            continue
        for piece in _fit_units(block.text, budget):
            t = count_tokens(piece)
            if buffer and size + t > budget:
                flush(keep_overlap=True)
                if buffer and size + t > budget:
                    # The carried-over tail plus this piece would break the budget: skip overlap.
                    buffer, size = [], 0
            buffer.append(piece)
            size += t
    flush(keep_overlap=False)

    raw = _absorb_slivers(raw, cfg.min_tokens, budget)

    chunks: list[Chunk] = []
    for ordinal, (kind, text) in enumerate(raw):
        if count_tokens(text) == 0:
            continue
        meta = ChunkMetadata(
            ticker=filing.ticker,
            cik=filing.cik,
            company=filing.company,
            form=filing.form,
            fiscal_year=filing.fiscal_year,
            fiscal_period=filing.fiscal_period,
            accession=filing.accession,
            item=section.item,
            item_title=section.title,
            chunk_type=kind,
            filed=filing.filed,
            source_url=filing.url,
            ordinal=ordinal,
            token_count=count_tokens(text),
        )
        chunk = Chunk(
            id=Chunk.make_id(filing.accession, section.item, ordinal, text),
            text=text,
            metadata=meta,
        )
        chunks.append(add_context_header(chunk) if cfg.context_header else chunk)
    return chunks
