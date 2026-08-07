from time import perf_counter

from texdiff.compare import compare_documents
from texdiff.models import BlockKind, ChangeKind, TableData, TextBlock


def block(index: int, text: str, kind: BlockKind = BlockKind.PARAGRAPH, **kwargs):
    return TextBlock(index, kind, text, **kwargs)


def test_word_diff():
    rows, stats = compare_documents(
        [block(0, "The method uses two reviewers.")],
        [block(0, "The revised method uses three reviewers.")],
    )
    assert stats.modified == 1
    assert "<del>two</del>" in rows[0].old_html
    assert "<ins>three</ins>" in rows[0].new_html


def test_add_delete():
    rows, stats = compare_documents(
        [block(0, "Shared."), block(1, "Removed paragraph.")],
        [block(0, "Shared."), block(1, "Added unrelated experiments.")],
        match_threshold=0.55,
    )
    assert stats.added == 1 and stats.deleted == 1


def test_move():
    old = [block(0, "Alpha."), block(1, "A long reusable paragraph with identical wording."), block(2, "Omega.")]
    new = [block(0, "Alpha."), block(1, "Omega."), block(2, "A long reusable paragraph with identical wording.")]
    rows, stats = compare_documents(old, new)
    assert stats.moved == 1
    assert next(row for row in rows if row.change == ChangeKind.MOVED).movement_note


def test_context():
    old = [block(i, f"Paragraph {i} is unchanged.") for i in range(8)]
    new = list(old)
    new[4] = block(4, "Paragraph four is substantially changed.")
    rows, _ = compare_documents(old, new, context=1)
    assert not rows[0].visible and rows[3].visible and rows[5].visible


def test_cjk():
    rows, stats = compare_documents([block(0, "该方法具有较好的稳定性。")], [block(0, "该方法具有很好的可靠性。")])
    assert stats.modified == 1
    assert rows[0].old_html.startswith("该方法具有<del>较</del>好的")


def test_formula_source_is_compared():
    rows, stats = compare_documents(
        [block(0, r"F_1=2PR/(P+R)", BlockKind.MATH)],
        [block(0, r"F_1=2PR/(P+R+\epsilon)", BlockKind.MATH)],
    )
    assert stats.modified == 1
    assert "Formula source" in rows[0].new_html
    assert "epsilon" in rows[0].new_html


def test_table_cell_diff_html():
    old_table = TableData((("Method", "F1"), ("Base", "88.0"), ("Ours", "89.3")), "Scores")
    new_table = TableData((("Method", "F1"), ("Base", "88.4"), ("Ours-v2", "91.1")), "Scores")
    old = block(0, old_table.flat_text, BlockKind.TABLE, table=old_table)
    new = block(0, new_table.flat_text, BlockKind.TABLE, table=new_table)
    rows, stats = compare_documents([old], [new])
    assert stats.modified == 1
    assert '<table class="latex-table">' in rows[0].new_html
    assert "cell-modified" in rows[0].new_html
    assert "<ins>4</ins>" in rows[0].new_html



def test_far_moved_exact_paragraph_does_not_break_table_alignment():
    old_table = TableData((("Method", "F1"), ("Base", "88.0"), ("Ours", "89.3")), "Scores")
    new_table = TableData((("Method", "F1"), ("Base", "88.4"), ("Ours-v2", "91.1")), "Scores")
    moved_text = "This paragraph is unchanged but moves past the entire methods section."
    old = [
        block(0, "Introduction", BlockKind.HEADING),
        block(1, "Opening text."),
        block(2, moved_text),
        block(3, "Method", BlockKind.HEADING),
        block(4, "Method text with old setting."),
        block(5, r"F_1=2PR/(P+R)", BlockKind.MATH),
        block(6, old_table.flat_text, BlockKind.TABLE, table=old_table),
        block(7, "Conclusion", BlockKind.HEADING),
    ]
    new = [
        block(0, "Introduction", BlockKind.HEADING),
        block(1, "Opening text revised."),
        block(2, "Method", BlockKind.HEADING),
        block(3, "Method text with revised setting."),
        block(4, r"F_1=2PR/(P+R+\epsilon)", BlockKind.MATH),
        block(5, new_table.flat_text, BlockKind.TABLE, table=new_table),
        block(6, moved_text),
        block(7, "Conclusion", BlockKind.HEADING),
    ]

    rows, stats = compare_documents(old, new)

    table_rows = [row for row in rows if row.old and row.old.kind == BlockKind.TABLE]
    assert len(table_rows) == 1
    assert table_rows[0].new and table_rows[0].new.kind == BlockKind.TABLE
    assert table_rows[0].change == ChangeKind.MODIFIED
    assert "cell-modified" in table_rows[0].new_html
    assert stats.moved == 1

def test_large_all_modified_alignment_is_bounded():
    repeated = " ".join(["structured configuration workflow status validation"] * 14)
    old = [block(i, f"Paragraph {i}. {repeated} old-value-{i}.") for i in range(120)]
    new = [block(i, f"Paragraph {i}. {repeated} revised-value-{i}.") for i in range(120)]
    start = perf_counter()
    rows, stats = compare_documents(old, new)
    elapsed = perf_counter() - start
    assert stats.modified == 120 and len(rows) == 120
    assert elapsed < 5.0
