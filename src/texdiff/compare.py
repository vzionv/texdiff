"""Fast semantic block alignment and token-level highlighting."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
import html
import re
import unicodedata

from rapidfuzz.fuzz import ratio as rapid_ratio

from .models import BlockKind, ChangeKind, DiffRow, DiffStats, TextBlock
from .table_diff import render_table_html

_TOKEN_RE = re.compile(
    r"\s+|[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]"
    r"|[\w]+(?:['’\-][\w]+)*|[^\w\s]",
    re.UNICODE,
)
_WORD_RE = re.compile(
    r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\u3040-\u30FF\uAC00-\uD7AF]"
    r"|[\w]+(?:['’\-][\w]+)*",
    re.UNICODE,
)
_PROSE_KINDS = {BlockKind.PARAGRAPH, BlockKind.LIST_ITEM, BlockKind.QUOTE}


@dataclass(frozen=True, slots=True)
class _Features:
    key: str
    tokens: frozenset[str]
    length: int
    kind: BlockKind


def comparison_key(block: TextBlock) -> str:
    text = block.table.flat_text if block.table is not None else block.text
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    if block.kind in {BlockKind.MATH, BlockKind.CODE, BlockKind.TABLE}:
        return text
    return re.sub(r"[^\w\s\\()\[\]{}^_+=<>|&%$#.,:;!?/-]", "", text)


def _features(block: TextBlock) -> _Features:
    key = comparison_key(block)
    tokens = frozenset(token.casefold() for token in _WORD_RE.findall(key))
    return _Features(key, tokens, len(key), block.kind)


def _compatible(a: BlockKind, b: BlockKind) -> bool:
    if a == b:
        return True
    return a in _PROSE_KINDS and b in _PROSE_KINDS


def _similarity_from_features(a: _Features, b: _Features) -> float:
    if not _compatible(a.kind, b.kind):
        return 0.0
    if not a.key or not b.key:
        return 0.0
    if a.key == b.key:
        return 1.0
    length = min(a.length, b.length) / max(a.length, b.length)
    if length < 0.16:
        return 0.0
    union = a.tokens | b.tokens
    jaccard = len(a.tokens & b.tokens) / len(union) if union else 0.0
    # Avoid expensive character matching for obviously unrelated long paragraphs.
    if jaccard < 0.025 and min(a.length, b.length) > 48:
        return 0.08 * length
    # RapidFuzz performs the edit-distance component in optimized native code.
    # This keeps repeated comparisons predictable for long, repetitive blocks.
    character_ratio = rapid_ratio(a.key, b.key) / 100.0
    return 0.58 * character_ratio + 0.32 * jaccard + 0.10 * length


def block_similarity(old: TextBlock, new: TextBlock) -> float:
    return _similarity_from_features(_features(old), _features(new))


def _align_gap(
    old_blocks: list[TextBlock],
    new_blocks: list[TextBlock],
    old_features: list[_Features],
    new_features: list[_Features],
    threshold: float,
) -> list[tuple[TextBlock | None, TextBlock | None, float]]:
    """Banded dynamic programming for a gap between exact anchors.

    This computes only a narrow strip of candidate pairs, so a document with
    hundreds of blocks does not require a full all-pairs matrix.
    """
    n, m = len(old_blocks), len(new_blocks)
    if not n:
        return [(None, block, 0.0) for block in new_blocks]
    if not m:
        return [(block, None, 0.0) for block in old_blocks]

    gap = -0.44
    # The band expands for unequal lengths and very small documents, but remains
    # bounded for large documents.
    band = min(max(n, m), max(14, abs(n - m) + 10, int(max(n, m) * 0.08)))
    neg_inf = -1e18
    dp = [[neg_inf] * (m + 1) for _ in range(n + 1)]
    moves = [[""] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(n + 1):
        expected = round(i * m / max(n, 1))
        start = max(0, expected - band)
        end = min(m, expected + band)
        for j in range(start, end + 1):
            if i == 0 and j == 0:
                continue
            best = neg_inf
            direction = ""
            if i > 0 and dp[i - 1][j] > neg_inf / 2:
                best = dp[i - 1][j] + gap
                direction = "up"
            if j > 0 and dp[i][j - 1] > neg_inf / 2 and dp[i][j - 1] + gap > best:
                best = dp[i][j - 1] + gap
                direction = "left"
            if i > 0 and j > 0 and dp[i - 1][j - 1] > neg_inf / 2:
                sim = _similarity_from_features(old_features[i - 1], new_features[j - 1])
                score = 2.1 * sim - 1.0 if sim >= threshold else -1.1
                diagonal = dp[i - 1][j - 1] + score - 0.04 * abs(i / n - j / m)
                if diagonal >= best:
                    best = diagonal
                    direction = "diag"
            dp[i][j] = best
            moves[i][j] = direction

    # A very unusual reordering can leave the end outside the band. Fall back to
    # linear delete/add output; move detection can still pair matching blocks.
    if dp[n][m] <= neg_inf / 2:
        return [*( (block, None, 0.0) for block in old_blocks), *( (None, block, 0.0) for block in new_blocks)]

    aligned: list[tuple[TextBlock | None, TextBlock | None, float]] = []
    i, j = n, m
    while i or j:
        direction = moves[i][j]
        if direction == "diag":
            sim = _similarity_from_features(old_features[i - 1], new_features[j - 1])
            if sim >= threshold:
                aligned.append((old_blocks[i - 1], new_blocks[j - 1], sim))
            else:
                aligned.extend(((old_blocks[i - 1], None, 0.0), (None, new_blocks[j - 1], 0.0)))
            i -= 1
            j -= 1
        elif direction == "up":
            aligned.append((old_blocks[i - 1], None, 0.0))
            i -= 1
        elif direction == "left":
            aligned.append((None, new_blocks[j - 1], 0.0))
            j -= 1
        else:  # Defensive recovery for a malformed band boundary.
            if i:
                aligned.append((old_blocks[i - 1], None, 0.0))
                i -= 1
            elif j:
                aligned.append((None, new_blocks[j - 1], 0.0))
                j -= 1
    aligned.reverse()
    return aligned


def _stable_exact_anchors(
    old_features: list[_Features],
    new_features: list[_Features],
) -> list[tuple[int, int]]:
    """Return a monotonic chain of reliable exact-match anchors.

    A plain :class:`difflib.SequenceMatcher` may select a block that moved a long
    distance as a global anchor. Everything crossed by that block is then forced
    into separate delete/add gaps. We consider unique exact blocks near their
    expected relative position and select the maximum-weight increasing chain.
    Moved blocks are left for ``_pair_moves``.

    The weighted LIS implementation is ``O(k log m)`` where ``k`` is the number
    of candidate anchors, so it preserves the alignment improvement for long
    documents.
    """
    n, m = len(old_features), len(new_features)
    if not n or not m:
        return []

    old_locations: dict[tuple[BlockKind, str], list[int]] = defaultdict(list)
    new_locations: dict[tuple[BlockKind, str], list[int]] = defaultdict(list)
    for index, feature in enumerate(old_features):
        if feature.key:
            old_locations[(feature.kind, feature.key)].append(index)
    for index, feature in enumerate(new_features):
        if feature.key:
            new_locations[(feature.kind, feature.key)].append(index)

    tolerance = max(4.0, 0.12 * max(n, m))
    candidates: list[tuple[int, int, float]] = []
    for key, old_indices in old_locations.items():
        new_indices = new_locations.get(key, [])
        if len(old_indices) != 1 or len(new_indices) != 1:
            continue
        old_index, new_index = old_indices[0], new_indices[0]
        expected_new = old_index * m / n
        displacement = abs(new_index - expected_new)
        if displacement > tolerance:
            continue
        kind = key[0]
        kind_weight = 5.0 if kind == BlockKind.HEADING else 2.5 if kind in {BlockKind.MATH, BlockKind.TABLE} else 1.0
        stability_bonus = 1.0 - min(1.0, displacement / max(tolerance, 1.0))
        candidates.append((old_index, new_index, kind_weight + stability_bonus))

    candidates.sort(key=lambda item: (item[0], item[1]))
    if not candidates:
        return []

    # Fenwick tree of (best cumulative weight, candidate index), queried on new
    # indices strictly smaller than the current one.
    tree: list[tuple[float, int]] = [(0.0, -1)] * (m + 1)
    scores = [0.0] * len(candidates)
    previous = [-1] * len(candidates)

    def query(position: int) -> tuple[float, int]:
        best = (0.0, -1)
        while position > 0:
            if tree[position][0] > best[0]:
                best = tree[position]
            position -= position & -position
        return best

    def update(position: int, value: tuple[float, int]) -> None:
        while position <= m:
            if value[0] > tree[position][0]:
                tree[position] = value
            position += position & -position

    for candidate_index, (_, new_index, weight) in enumerate(candidates):
        best_score, predecessor = query(new_index)
        scores[candidate_index] = best_score + weight
        previous[candidate_index] = predecessor
        update(new_index + 1, (scores[candidate_index], candidate_index))

    end = max(range(len(candidates)), key=scores.__getitem__)
    anchors: list[tuple[int, int]] = []
    while end >= 0:
        old_index, new_index, _ = candidates[end]
        anchors.append((old_index, new_index))
        end = previous[end]
    anchors.reverse()
    return anchors


def _align_blocks(
    old_blocks: list[TextBlock],
    new_blocks: list[TextBlock],
    threshold: float,
) -> list[tuple[TextBlock | None, TextBlock | None, float]]:
    old_features = [_features(block) for block in old_blocks]
    new_features = [_features(block) for block in new_blocks]

    # Stable exact blocks split the document into small fuzzy-alignment gaps.
    # A final sentinel lets the same loop process the trailing gap.
    anchors = _stable_exact_anchors(old_features, new_features)
    aligned: list[tuple[TextBlock | None, TextBlock | None, float]] = []
    old_cursor = new_cursor = 0
    for old_anchor, new_anchor in [*anchors, (len(old_blocks), len(new_blocks))]:
        if old_anchor > old_cursor or new_anchor > new_cursor:
            aligned.extend(
                _align_gap(
                    old_blocks[old_cursor:old_anchor],
                    new_blocks[new_cursor:new_anchor],
                    old_features[old_cursor:old_anchor],
                    new_features[new_cursor:new_anchor],
                    threshold,
                )
            )
        if old_anchor < len(old_blocks) and new_anchor < len(new_blocks):
            aligned.append((old_blocks[old_anchor], new_blocks[new_anchor], 1.0))
            old_cursor = old_anchor + 1
            new_cursor = new_anchor + 1
        else:
            old_cursor = old_anchor
            new_cursor = new_anchor
    return aligned


def _pair_moves(
    aligned: list[tuple[TextBlock | None, TextBlock | None, float]],
    threshold: float,
) -> list[tuple[TextBlock | None, TextBlock | None, float, bool, str | None]]:
    deleted = [(i, old, _features(old)) for i, (old, new, _) in enumerate(aligned) if old and new is None]
    added = [(i, new, _features(new)) for i, (old, new, _) in enumerate(aligned) if new and old is None]
    if not deleted or not added:
        return [(old, new, sim, False, None) for old, new, sim in aligned]

    candidates: list[tuple[float, int, int, TextBlock]] = []
    added_by_key: dict[tuple[BlockKind, str], list[tuple[int, TextBlock, _Features]]] = defaultdict(list)
    token_index: dict[str, set[int]] = defaultdict(set)
    added_lookup: dict[int, tuple[TextBlock, _Features]] = {}
    for index, block, feature in added:
        added_by_key[(feature.kind, feature.key)].append((index, block, feature))
        added_lookup[index] = (block, feature)
        for token in feature.tokens:
            token_index[token].add(index)

    for old_index, old_block, old_feature in deleted:
        exact = added_by_key.get((old_feature.kind, old_feature.key), [])
        for new_index, _, _ in exact:
            candidates.append((1.0, old_index, new_index, old_block))
        possible: set[int] = set()
        for token in old_feature.tokens:
            possible.update(token_index.get(token, ()))
        if not possible and len(added) <= 40:
            possible = {index for index, _, _ in added}
        if len(possible) > 48:
            possible = set(
                sorted(
                    possible,
                    key=lambda index: abs(added_lookup[index][1].length - old_feature.length),
                )[:48]
            )
        for new_index in possible:
            new_block, new_feature = added_lookup[new_index]
            if new_feature.key == old_feature.key and new_feature.kind == old_feature.kind:
                continue
            sim = _similarity_from_features(old_feature, new_feature)
            if sim >= threshold:
                candidates.append((sim, old_index, new_index, old_block))

    candidates.sort(reverse=True, key=lambda item: item[0])
    used_old: set[int] = set()
    used_new: set[int] = set()
    by_new: dict[int, tuple[TextBlock, float]] = {}
    for sim, old_index, new_index, old_block in candidates:
        if old_index in used_old or new_index in used_new:
            continue
        used_old.add(old_index)
        used_new.add(new_index)
        by_new[new_index] = old_block, sim

    result: list[tuple[TextBlock | None, TextBlock | None, float, bool, str | None]] = []
    for index, (old, new, sim) in enumerate(aligned):
        if index in used_old:
            continue
        if index in by_new and new:
            moved_old, moved_sim = by_new[index]
            result.append((moved_old, new, moved_sim, True, f"moved from old block {moved_old.index + 1}"))
        else:
            result.append((old, new, sim, False, None))
    return result


def _render_token_diff(old_text: str, new_text: str) -> tuple[str, str]:
    old_tokens = _TOKEN_RE.findall(old_text)
    new_tokens = _TOKEN_RE.findall(new_text)
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    old_parts: list[str] = []
    new_parts: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        old_chunk = html.escape("".join(old_tokens[i1:i2]))
        new_chunk = html.escape("".join(new_tokens[j1:j2]))
        if tag == "equal":
            old_parts.append(old_chunk)
            new_parts.append(new_chunk)
        elif tag == "delete":
            old_parts.append(f"<del>{old_chunk}</del>")
        elif tag == "insert":
            new_parts.append(f"<ins>{new_chunk}</ins>")
        else:
            old_parts.append(f"<del>{old_chunk}</del>")
            new_parts.append(f"<ins>{new_chunk}</ins>")
    return "".join(old_parts), "".join(new_parts)


def _render_math(value: str, css_class: str = "") -> str:
    return (
        f'<div class="math-source {css_class}"><span class="kind-label">Formula source</span><br>'
        f'<code>{value}</code></div>'
    )


def _render_code(value: str) -> str:
    return f'<pre class="code-block">{value}</pre>'


def _render_pair(old: TextBlock, new: TextBlock, same: bool) -> tuple[str, str]:
    if old.kind == BlockKind.TABLE and new.kind == BlockKind.TABLE:
        return (
            render_table_html(old.table, new.table, side="old", token_diff=_render_token_diff),
            render_table_html(old.table, new.table, side="new", token_diff=_render_token_diff),
        )
    old_html, new_html = (html.escape(old.text), html.escape(new.text)) if same else _render_token_diff(old.text, new.text)
    if old.kind == BlockKind.MATH and new.kind == BlockKind.MATH:
        return _render_math(old_html), _render_math(new_html)
    if old.kind == BlockKind.CODE and new.kind == BlockKind.CODE:
        return _render_code(old_html), _render_code(new_html)
    return old_html, new_html


def _render_single(block: TextBlock, change: ChangeKind, side: str) -> str:
    if block.kind == BlockKind.TABLE:
        return render_table_html(
            block.table if side == "old" else None,
            block.table if side == "new" else None,
            side=side,
            token_diff=_render_token_diff,
        )
    escaped = html.escape(block.text)
    tagged = f"<del>{escaped}</del>" if change == ChangeKind.DELETED else f"<ins>{escaped}</ins>"
    if block.kind == BlockKind.MATH:
        return _render_math(tagged)
    if block.kind == BlockKind.CODE:
        return _render_code(tagged)
    return tagged


def compare_documents(
    old_blocks: list[TextBlock],
    new_blocks: list[TextBlock],
    *,
    match_threshold: float = 0.38,
    move_threshold: float = 0.78,
    context: int = 1,
) -> tuple[list[DiffRow], DiffStats]:
    if not (0 <= match_threshold <= 1 and 0 <= move_threshold <= 1):
        raise ValueError("similarity thresholds must be between 0 and 1")
    if context < 0:
        raise ValueError("context must be non-negative")

    rows: list[DiffRow] = []
    paired = _pair_moves(_align_blocks(old_blocks, new_blocks, match_threshold), move_threshold)
    for old, new, similarity, moved, note in paired:
        if old is None and new:
            row = DiffRow(None, new, ChangeKind.ADDED, new_html=_render_single(new, ChangeKind.ADDED, "new"))
        elif old and new is None:
            row = DiffRow(old, None, ChangeKind.DELETED, old_html=_render_single(old, ChangeKind.DELETED, "old"))
        elif old and new:
            same = comparison_key(old) == comparison_key(new)
            change = ChangeKind.MOVED if moved else ChangeKind.UNCHANGED if same else ChangeKind.MODIFIED
            old_html, new_html = _render_pair(old, new, same)
            row = DiffRow(old, new, change, similarity, old_html, new_html, movement_note=note)
        else:
            continue
        rows.append(row)

    changed = {index for index, row in enumerate(rows) if row.change != ChangeKind.UNCHANGED}
    if changed:
        visible: set[int] = set()
        for index in changed:
            visible.update(range(max(0, index - context), min(len(rows), index + context + 1)))
        for index, row in enumerate(rows):
            row.visible = index in visible

    counts = {kind: 0 for kind in ChangeKind}
    for row in rows:
        counts[row.change] += 1
    return rows, DiffStats(
        len(old_blocks), len(new_blocks), counts[ChangeKind.UNCHANGED], counts[ChangeKind.MODIFIED],
        counts[ChangeKind.ADDED], counts[ChangeKind.DELETED], counts[ChangeKind.MOVED],
    )
