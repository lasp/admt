"""Tests for the shared redo-output rewrite function."""

from __future__ import annotations

from admt.adapters.redo_output import rewrite_line, rewrite_line_terse


def test_rewrite_line_drops_top_level_redo_header():
    # Exactly two spaces = redo's "currently processing X" outer marker.
    assert rewrite_line("redo  all") is None


def test_rewrite_line_drops_top_level_header_with_trailing_newline():
    # Real streaming input arrives with trailing newlines.
    assert rewrite_line("redo  all\n") is None


def test_rewrite_line_transforms_first_level_nested_with_default_spacing():
    # Four spaces in redo = first-level rebuild; separator is a single space.
    assert rewrite_line("redo    build/src/foo.adb") == "admt build build/src/foo.adb"


def test_rewrite_line_encodes_depth_in_separator_between_verb_and_target():
    # Verb is flush-left; deeper depths push the target right via extra
    # spaces between ``admt build`` and the target.
    assert rewrite_line("redo      types/foo.html") == "admt build   types/foo.html"
    assert rewrite_line("redo        types/foo.yaml") == "admt build     types/foo.yaml"
    assert rewrite_line("redo          types/foo.o") == "admt build       types/foo.o"


def test_rewrite_line_maps_named_target_single_space_listing():
    # `redo what` lists entries with a single space; map to admt equivalent.
    assert rewrite_line("redo all") == "admt build"


def test_rewrite_line_maps_all_suffix_variants():
    assert rewrite_line("redo test_all") == "admt test --all"
    assert rewrite_line("redo style_all") == "admt style --all"


def test_rewrite_line_falls_back_to_admt_build_for_unknown_target():
    # Unknown name -> treat as a build target (BuildCommand forwards it).
    assert rewrite_line("redo pretty") == "admt build pretty"


def test_rewrite_line_strips_ansi_before_matching():
    # Redo wraps its progress in green ANSI on a TTY; we match plain text.
    assert rewrite_line("\x1b[32mredo\x1b[0m all") == "admt build"


def test_rewrite_line_passes_through_non_redo_lines():
    # Compiler warnings or arbitrary tool output must pass through untouched.
    original = "gnatmake: warning: something happened\n"
    assert rewrite_line(original) == original


def test_rewrite_line_blank_input_passes_through():
    # Blank lines are tool-output whitespace; let the caller decide to filter.
    assert rewrite_line("") == ""
    assert rewrite_line("\n") == "\n"


# ----- rewrite_line_terse: streaming path drops the `admt ` prefix -----


def test_rewrite_line_terse_strips_admt_prefix_from_verbs():
    assert rewrite_line_terse("redo all") == "build"
    assert rewrite_line_terse("redo test") == "test"
    assert rewrite_line_terse("redo test_all") == "test --all"


def test_rewrite_line_terse_strips_admt_prefix_from_build_fallback():
    assert rewrite_line_terse("redo    build/src/foo.adb") == "build build/src/foo.adb"


def test_rewrite_line_terse_preserves_depth_separator_while_stripping_prefix():
    # Deep rebuilds: separator-encoded depth preserved, ``admt `` prefix dropped.
    assert rewrite_line_terse("redo      types/foo.html") == "build   types/foo.html"
    assert rewrite_line_terse("redo        types/foo.yaml") == "build     types/foo.yaml"


def test_rewrite_line_terse_propagates_none_for_dropped_header():
    # 2-space redo header is still dropped in terse mode.
    assert rewrite_line_terse("redo  all") is None


def test_rewrite_line_terse_passes_through_non_redo_lines_unchanged():
    # Pass-through lines don't carry the ``admt `` prefix, so removeprefix is a no-op.
    original = "gnatmake: warning: something\n"
    assert rewrite_line_terse(original) == original
