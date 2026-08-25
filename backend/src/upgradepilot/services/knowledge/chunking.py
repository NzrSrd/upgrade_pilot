"""Splitting a corpus document into the units that are embedded and cited.

A chunk is what a `SourceRef` points at, which fixes the priorities. Losing
text is worse than chunking it badly: a dropped paragraph makes the change it
documents permanently unretrievable while the corpus still reports the
document as ingested. Overshooting the size budget is worse than cutting a
paragraph in half: an oversized chunk retrieves less precisely, but a halved
one is cited as a quotation the author never wrote.

So `max_chars` is a *target*. Boundaries fall between paragraphs, never
inside one and never inside a fenced code block, and a single paragraph
larger than the budget is emitted whole.
"""

from upgradepilot.models.knowledge import CorpusDocument, DocumentChunk

MAX_CHUNK_CHARS = 1200
"""Target chunk size in characters.

Sized for this corpus rather than borrowed from a default. Documents are
authored one breaking change each (spec §7.2), so the common document is a
few hundred characters and lands in a single chunk -- which is what we want,
because a change split in two can have its 'old form' retrieved without its
'new form'. The budget exists for the long ones: an ADR, or a guide carrying
several code blocks.

Published as a module constant because ingestion and any later re-chunking
must agree on it. `chunk_id` is positional, so two components using different
budgets would mint different citation keys for identical content.
"""

FENCE = "```"


def _paragraphs(body: str) -> list[str]:
    """Split on blank lines, treating a fenced code block as one paragraph.

    The fence tracking is why this is not `body.split("\\n\\n")`: a blank
    line inside a ``` block is ordinary formatting in a code sample, and
    splitting there would let a later boundary fall between a fence's two
    halves.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    for line in body.splitlines():
        if line.lstrip().startswith(FENCE):
            in_fence = not in_fence
            current.append(line)
            continue
        if not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)

    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def chunk_document(
    document: CorpusDocument, *, max_chars: int = MAX_CHUNK_CHARS
) -> tuple[DocumentChunk, ...]:
    """Split `document.body` into ordered, deterministically-named chunks.

    Greedy: paragraphs are packed in authored order until the next one would
    exceed `max_chars`, then a new chunk starts. Deterministic by
    construction -- the packing depends only on the body and the budget, and
    `chunk_id` is `{source_id}#chunk-{ordinal}` -- so re-ingesting unchanged
    content re-mints the same citation keys.
    """
    blocks = _paragraphs(document.body)
    if not blocks:
        # `CorpusDocument.body` is a non-blank string, so this is
        # unreachable via `parse_document`. Kept as a raise rather than an
        # empty tuple: a document that silently contributes no chunks is
        # ingested, counted, and invisible to every query.
        raise ValueError(f"{document.path}: body split into no paragraphs")

    grouped: list[list[str]] = [[]]
    length = 0
    for block in blocks:
        addition = len(block) + (2 if grouped[-1] else 0)
        if grouped[-1] and length + addition > max_chars:
            grouped.append([block])
            length = len(block)
        else:
            grouped[-1].append(block)
            length += addition

    return tuple(
        DocumentChunk(
            chunk_id=f"{document.source_id}#chunk-{ordinal}",
            source_id=document.source_id,
            ordinal=ordinal,
            text="\n\n".join(group),
        )
        for ordinal, group in enumerate(grouped)
    )
