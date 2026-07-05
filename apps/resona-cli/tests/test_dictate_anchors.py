"""Tests for resona_cli.dictate_anchors — pure, no Textual dependency."""
import pytest

from resona_cli.dictate_anchors import AnchorTracker, adjust_location


# ── adjust_location ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "loc, edit_top, edit_bottom, new_end, expected",
    [
        # Edit entirely after the location on the same row: no shift.
        ((0, 2), (0, 5), (0, 5), (0, 8), (0, 2)),
        # Insert exactly at the location (edit_top == edit_bottom == loc):
        # the location sits at the boundary and shifts by the insert length.
        ((0, 5), (0, 5), (0, 5), (0, 8), (0, 8)),
        # Insert entirely before the location on the same row: column shifts
        # by the inserted length.
        ((0, 10), (0, 2), (0, 2), (0, 5), (0, 13)),
        # Deletion entirely before the location on the same row: column
        # shrinks by the deleted length.
        ((0, 10), (0, 2), (0, 6), (0, 2), (0, 6)),
        # Edit on an earlier row: row is unaffected (edit_bottom row < loc row).
        ((3, 4), (0, 0), (0, 5), (0, 2), (3, 4)),
        # Multi-line insert before the location: row offset applied, and since
        # the edit's bottom row differs from the location's row, column is untouched.
        ((3, 4), (1, 0), (1, 0), (4, 0), (6, 4)),
        # Edit spans multiple rows ending on the location's row before the
        # location's column: row shifts, and since edit_bottom row == loc
        # row, the column also shifts by the same offset.
        ((3, 4), (1, 0), (3, 2), (1, 10), (1, 12)),
        # A deletion whose range fully engulfs the location (location's row
        # is strictly between edit_top's and edit_bottom's rows) leaves the
        # location completely unchanged — a known limitation shared with
        # Textual's own cursor/selection adjustment: only locations on or
        # after the edit's bottom row get corrected, so an engulfed location
        # may end up pointing at stale/nonexistent content post-edit.
        ((2, 3), (0, 0), (5, 0), (0, 0), (2, 3)),
    ],
)
def test_adjust_location(loc, edit_top, edit_bottom, new_end, expected):
    assert adjust_location(loc, edit_top, edit_bottom, new_end) == expected


def test_adjust_location_noop_when_edit_after_location():
    # Edit strictly after the location on a later row: no change at all.
    loc = (0, 2)
    assert adjust_location(loc, (5, 0), (5, 3), (5, 10)) == loc


# ── AnchorTracker ───────────────────────────────────────────────────────────

def test_register_and_get():
    tracker = AnchorTracker()
    tracker.register("a", (0, 0), (0, 5))
    assert tracker.get("a") == ((0, 0), (0, 5))
    assert len(tracker) == 1


def test_get_missing_returns_none():
    tracker = AnchorTracker()
    assert tracker.get("missing") is None


def test_pop_removes_and_returns():
    tracker = AnchorTracker()
    tracker.register("a", (0, 0), (0, 5))
    assert tracker.pop("a") == ((0, 0), (0, 5))
    assert tracker.get("a") is None
    assert len(tracker) == 0


def test_pop_missing_returns_none():
    tracker = AnchorTracker()
    assert tracker.pop("missing") is None


def test_apply_edit_shifts_registered_span():
    tracker = AnchorTracker()
    tracker.register("a", (0, 10), (0, 20))
    # An insertion of 3 chars before the span shifts both endpoints.
    tracker.apply_edit((0, 2), (0, 2), (0, 5))
    assert tracker.get("a") == ((0, 13), (0, 23))


def test_apply_edit_does_not_adjust_span_created_by_same_edit():
    """The edit that creates a span must not double-shift it.

    Mirrors real usage: register() is called *after* performing the insert,
    using the already-adjusted coordinates from the EditResult, so a
    subsequent apply_edit() for that same insert must never be applied to it.
    """
    tracker = AnchorTracker()
    # Simulate: insert placeholder at (0, 0)-(0, 10), then register.
    tracker.register("a", (0, 0), (0, 10))
    assert tracker.get("a") == ((0, 0), (0, 10))


def test_two_concurrent_segments_stay_independent():
    tracker = AnchorTracker()
    tracker.register("first", (0, 0), (0, 5))
    tracker.register("second", (1, 0), (1, 5))

    # Typing 4 characters at the very start of the document shifts "first"
    # (same row) but leaves "second" (a later row) untouched in column,
    # only its row is preserved since the edit doesn't touch row 1.
    tracker.apply_edit((0, 0), (0, 0), (0, 4))

    assert tracker.get("first") == ((0, 4), (0, 9))
    assert tracker.get("second") == ((1, 0), (1, 5))


def test_resolving_one_segment_shifts_the_other_pending_segment():
    """When segment A's transcript is inserted, any still-pending segment B
    whose span comes after A's must shift to account for the new text.
    """
    tracker = AnchorTracker()
    tracker.register("a", (0, 0), (0, 12))  # placeholder for "a"
    tracker.register("b", (0, 20), (0, 32))  # placeholder for "b", later in the line

    # "a" resolves: its 12-char placeholder is replaced with a 30-char transcript.
    a_start, a_end = tracker.pop("a")
    tracker.apply_edit(a_start, a_end, (0, a_start[1] + 30))

    assert tracker.get("a") is None
    # "b" shifts right by (30 - 12) = 18 columns.
    assert tracker.get("b") == ((0, 38), (0, 50))


# ── inclusive vs exclusive boundary (the span-engulfing bug) ────────────────

def test_inclusive_boundary_pushes_forward_when_edit_lands_exactly_on_it():
    # An insertion landing exactly on the location shifts it forward — this
    # is what a span's *start* (and a real cursor) must do, so text typed
    # right before a placeholder ends up excluded from (before) the span.
    assert adjust_location((0, 5), (0, 5), (0, 5), (0, 8), inclusive=True) == (0, 8)


def test_exclusive_boundary_stays_put_when_edit_lands_exactly_on_it():
    # A span's *end* must NOT grow when an edit lands exactly on it — used
    # when the user keeps typing immediately after a dictation placeholder.
    assert adjust_location((0, 5), (0, 5), (0, 5), (0, 8), inclusive=False) == (0, 5)


def test_exclusive_boundary_still_shifts_for_edits_strictly_before_it():
    # Exclusivity only changes the exact-boundary case — an edit that's
    # unambiguously before the location still shifts it either way.
    assert adjust_location((0, 10), (0, 2), (0, 2), (0, 5), inclusive=False) == (0, 13)


def test_typing_immediately_after_a_placeholder_does_not_grow_its_span():
    """Regression test for the span-engulfing bug: if the span's end used the
    same (inclusive) rule as its start, text the user types right after a
    still-pending placeholder would be absorbed into the span and destroyed
    when the placeholder is later replaced with the real transcript.
    """
    tracker = AnchorTracker()
    # Placeholder "a" occupies columns [4, 16) — e.g. "AAA " + 12-char marker.
    tracker.register("a", (0, 4), (0, 16))

    # User types 6 more characters immediately after the placeholder (cursor
    # was sitting right at its end).
    tracker.apply_edit((0, 16), (0, 16), (0, 22))

    start, end = tracker.get("a")
    assert start == (0, 4)  # untouched: edit was well after start
    assert end == (0, 16)  # untouched: exclusive rule refuses to grow the span


def test_another_segments_placeholder_inserted_at_this_ones_end_does_not_grow_it():
    """Same guarantee, but the thing landing at the boundary is another
    segment's placeholder rather than user-typed text — segment spans must
    never overlap even when placed back-to-back with no gap.
    """
    tracker = AnchorTracker()
    tracker.register("a", (0, 4), (0, 16))

    # Segment "b"'s placeholder (8 chars) is inserted exactly at "a"'s end.
    tracker.apply_edit((0, 16), (0, 16), (0, 24))
    tracker.register("b", (0, 16), (0, 24))

    assert tracker.get("a") == ((0, 4), (0, 16))
    assert tracker.get("b") == ((0, 16), (0, 24))
