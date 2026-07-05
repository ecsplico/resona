"""Pure logic for tracking document positions ("anchors") across text edits.

No dependency on Textual — a dictation segment's insertion point is
represented as a ``(start, end)`` span that must stay correct as the
surrounding document is edited while the segment's transcription is still
in flight (by the user typing, or by another segment's transcript landing).

``adjust_location`` mirrors the selection-adjustment rule in Textual's own
``textual.document._edit.Edit.do`` — the same algorithm ``TextArea`` uses to
keep the user's cursor sane across a programmatic edit it didn't cause.
"""
from __future__ import annotations

Location = tuple[int, int]  # (row, col)


def adjust_location(
    loc: Location, edit_top: Location, edit_bottom: Location, new_end: Location,
    *, inclusive: bool = True,
) -> Location:
    """Shift *loc* to account for an edit spanning ``[edit_top, edit_bottom)``
    that replaced that range with text now ending at *new_end*.

    *inclusive* controls what happens when an edit lands exactly at *loc*
    (same row, ``edit_bottom`` column equal to *loc*'s column):

    - ``True`` (the default, and what Textual itself uses for the cursor):
      *loc* is pushed forward along with the inserted text — appropriate for
      a cursor, or for the *start* of a tracked span, where content typed at
      that exact boundary should end up excluded from (before) the span.
    - ``False``: *loc* stays put. Use this for the *end* of a tracked span
      representing replaceable placeholder text — otherwise, content the
      user types immediately after the placeholder would silently be
      absorbed into the span and get destroyed when the placeholder is
      later replaced with the real transcript.
    """
    row, col = loc
    bottom_row, bottom_col = edit_bottom
    boundary_hit = bottom_col <= col if inclusive else bottom_col < col
    if row == bottom_row and boundary_hit:
        col = col + (new_end[1] - bottom_col)
    if bottom_row <= row:
        row = row + (new_end[0] - bottom_row)
    return (row, col)


class AnchorTracker:
    """Tracks named ``(start, end)`` spans in a document, keeping them valid across edits.

    ``start`` uses inclusive (right-gravity) adjustment and ``end`` uses
    exclusive (left-gravity) adjustment — see `adjust_location` — so a span
    always bounds exactly its original placeholder text, never text typed
    immediately before or after it.
    """

    def __init__(self) -> None:
        self._spans: dict[str, tuple[Location, Location]] = {}

    def register(self, segment_id: str, start: Location, end: Location) -> None:
        self._spans[segment_id] = (start, end)

    def apply_edit(self, edit_top: Location, edit_bottom: Location, new_end: Location) -> None:
        """Adjust every tracked span for an edit that already happened elsewhere in the document."""
        for segment_id, (start, end) in self._spans.items():
            self._spans[segment_id] = (
                adjust_location(start, edit_top, edit_bottom, new_end, inclusive=True),
                adjust_location(end, edit_top, edit_bottom, new_end, inclusive=False),
            )

    def get(self, segment_id: str) -> tuple[Location, Location] | None:
        return self._spans.get(segment_id)

    def pop(self, segment_id: str) -> tuple[Location, Location] | None:
        return self._spans.pop(segment_id, None)

    def __len__(self) -> int:
        return len(self._spans)
