"""Design tokens against accessibility rules and the API's vocabulary. Standard library only.

Run from the repository root:
    python3 -m unittest discover -s tests

What this proves: the token values satisfy the stated WCAG and colour-vision thresholds as
*computed here*, every API entity kind and provenance label has a visual encoding, and the generated
CSS/TypeScript match tokens.json. What it does not prove: that any rendered screen looks right.
"""

import _paths  # noqa: F401  (puts the tooling directories on sys.path)

import re
import unittest
from pathlib import Path

from colorscience import contrast_ratio, delta_e, oklch, worst_cvd_pair, worst_pair
from tokengen import kebab, load_tokens, render_css, render_typescript

from vyron_api.api import limits
from vyron_api.domain.entities import EntityKind
from vyron_api.domain.provenance import Provenance

ROOT = Path(__file__).resolve().parents[1]
TOKENS = load_tokens()
SURFACES = TOKENS["color"]["surface"]
HEX = re.compile(r"^#[0-9a-f]{6}$")

# Numbers produced by the separate dataviz validator on 2026-10-09, used to pin this module's maths.
# (Dark mode, surface #0a0e14.) If colorscience.py drifts from the validator these tests fail.
PINNED_REFERENCE_VALUES = {
    "group_palette_worst_cvd": 6.9,  # protan, #008300 vs #c98500
    "group_palette_worst_normal": 19.3,  # #d55181 vs #c98500
    "group_palette_worst_tritan": 8.7,
    "eight_hue_all_pairs_worst_cvd": 1.6,  # deutan, #d55181 vs #199e70
    "eight_hue_all_pairs_worst_normal": 7.1,  # #e66767 vs #d95926
}
EIGHT_HUE_REFERENCE = [
    "#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767",
]  # fmt: skip


def group_colors() -> list[str]:
    data = TOKENS["data"]
    return [data["groups"][name]["color"] for name in data["groupOrder"]]


def px(value: str) -> float:
    assert value.endswith("px"), value
    return float(value[:-2])


class ColourMathTest(unittest.TestCase):
    def test_contrast_ratio_known_values(self) -> None:
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=6)
        self.assertAlmostEqual(contrast_ratio("#ffffff", "#ffffff"), 1.0, places=6)
        self.assertAlmostEqual(contrast_ratio("#767676", "#ffffff"), 4.54, places=2)  # the classic AA grey

    def test_contrast_is_symmetric(self) -> None:
        self.assertEqual(contrast_ratio("#123456", "#abcdef"), contrast_ratio("#abcdef", "#123456"))

    def test_delta_e_identity_and_symmetry(self) -> None:
        self.assertEqual(delta_e("#336699", "#336699"), 0.0)
        self.assertAlmostEqual(delta_e("#336699", "#cc9933"), delta_e("#cc9933", "#336699"))

    def test_rejects_malformed_colours(self) -> None:
        for bad in ("fff", "#fff", "#12345g", "red", "#1234567"):
            with self.assertRaises(ValueError, msg=bad):
                contrast_ratio(bad, "#000000")

    def test_unknown_vision_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            delta_e("#000000", "#ffffff", "monochromacy")

    def test_pinned_to_the_separate_validator(self) -> None:
        cvd = worst_cvd_pair(group_colors())
        self.assertAlmostEqual(cvd[0], PINNED_REFERENCE_VALUES["group_palette_worst_cvd"], delta=0.05)
        self.assertEqual({cvd[2], cvd[3]}, {"#008300", "#c98500"})
        normal = worst_pair(group_colors())
        self.assertAlmostEqual(normal[0], PINNED_REFERENCE_VALUES["group_palette_worst_normal"], delta=0.05)
        tritan = worst_pair(group_colors(), "tritan")[0]
        self.assertAlmostEqual(tritan, PINNED_REFERENCE_VALUES["group_palette_worst_tritan"], delta=0.05)
        cvd8 = worst_cvd_pair(EIGHT_HUE_REFERENCE)
        self.assertAlmostEqual(cvd8[0], PINNED_REFERENCE_VALUES["eight_hue_all_pairs_worst_cvd"], delta=0.05)
        normal8 = worst_pair(EIGHT_HUE_REFERENCE)
        self.assertAlmostEqual(normal8[0], PINNED_REFERENCE_VALUES["eight_hue_all_pairs_worst_normal"], delta=0.05)


class ColourTokenTest(unittest.TestCase):
    def test_every_colour_is_lowercase_six_digit_hex(self) -> None:
        values = [v for group in TOKENS["color"].values() for v in group.values()]
        values += [g["color"] for g in TOKENS["data"]["groups"].values()]
        self.assertGreater(len(values), 15)
        for value in values:
            self.assertRegex(value, HEX)

    def test_text_meets_wcag_aa_on_every_surface(self) -> None:
        minimums = {"bone": 7.0, "ash": 4.5, "dust": 4.5}
        for name, minimum in minimums.items():
            for surface, surface_hex in SURFACES.items():
                ratio = contrast_ratio(TOKENS["color"]["text"][name], surface_hex)
                self.assertGreaterEqual(ratio, minimum, f"text.{name} on {surface}: {ratio:.2f}")

    def test_accent_and_status_text_meet_aa_on_every_surface(self) -> None:
        for family, names in (("accent", ("signal", "signal-hover")), ("status", tuple(TOKENS["color"]["status"]))):
            for name in names:
                for surface, surface_hex in SURFACES.items():
                    ratio = contrast_ratio(TOKENS["color"][family][name], surface_hex)
                    self.assertGreaterEqual(ratio, 4.5, f"{family}.{name} on {surface}: {ratio:.2f}")

    def test_text_on_the_accent_fill_meets_aa(self) -> None:
        accent = TOKENS["color"]["accent"]
        self.assertGreaterEqual(contrast_ratio(accent["signal-ink"], accent["signal"]), 4.5)
        self.assertGreaterEqual(contrast_ratio(accent["signal-ink"], accent["signal-hover"]), 4.5)

    def test_control_borders_meet_wcag_non_text_contrast(self) -> None:
        for surface, surface_hex in SURFACES.items():
            ratio = contrast_ratio(TOKENS["color"]["line"]["control"], surface_hex)
            self.assertGreaterEqual(ratio, 3.0, f"line.control on {surface}: {ratio:.2f}")

    def test_decorative_lines_are_not_used_as_control_borders(self) -> None:
        # hairline and strong are structural; they are allowed to be below 3:1 only because the
        # component specs forbid using them as the sole boundary of an interactive control.
        line = TOKENS["color"]["line"]
        self.assertLess(contrast_ratio(line["hairline"], SURFACES["basalt"]), 3.0)
        self.assertLess(contrast_ratio(line["strong"], SURFACES["basalt"]), 3.0)
        self.assertIn("never the only boundary", (ROOT / "docs" / "COMPONENTS.md").read_text(encoding="utf-8"))

    def test_surfaces_step_up_in_lightness(self) -> None:
        lightness = [oklch(SURFACES[name])[0] for name in ("ink", "basalt", "slate")]
        self.assertEqual(lightness, sorted(lightness))
        self.assertLess(lightness[-1], 0.3, "surfaces must stay near-black")


class DataPaletteTest(unittest.TestCase):
    def test_groups_are_in_the_documented_order_and_complete(self) -> None:
        data = TOKENS["data"]
        self.assertEqual(data["groupOrder"], ["software", "intelligence", "organizations", "events"])
        self.assertEqual(set(data["groupOrder"]), set(data["groups"]))

    def test_marks_meet_graphical_contrast_on_every_surface(self) -> None:
        for colour in group_colors():
            for surface, surface_hex in SURFACES.items():
                self.assertGreaterEqual(contrast_ratio(colour, surface_hex), 3.0, f"{colour} on {surface}")

    def test_lightness_band_and_chroma_floor(self) -> None:
        for colour in group_colors():
            lightness, chroma, _ = oklch(colour)
            self.assertTrue(0.48 <= lightness <= 0.67, f"{colour} L={lightness:.3f}")
            self.assertGreaterEqual(chroma, 0.10, f"{colour} C={chroma:.3f}")

    def test_all_pairs_stay_distinct_for_full_colour_vision(self) -> None:
        self.assertGreaterEqual(worst_pair(group_colors())[0], 15.0)

    def test_all_pairs_clear_the_colour_blind_floor(self) -> None:
        worst = worst_cvd_pair(group_colors())[0]
        self.assertGreaterEqual(worst, 6.0)

    def test_colour_blind_floor_band_is_paired_with_a_second_encoding(self) -> None:
        # Below the 8.0 target the palette is only legal with secondary encoding. Shape is that
        # encoding, so every kind must carry one and no two kinds in a group may share it.
        if worst_cvd_pair(group_colors())[0] < 8.0:
            for kind, spec in TOKENS["data"]["kinds"].items():
                self.assertIn(spec["shape"], TOKENS["data"]["shapes"], kind)

    def test_the_eight_hue_reference_palette_is_rejected_for_the_graph(self) -> None:
        # Why four groups: with all 28 pairs in play the full reference set fails both gates.
        self.assertLess(worst_cvd_pair(EIGHT_HUE_REFERENCE)[0], 6.0)
        self.assertLess(worst_pair(EIGHT_HUE_REFERENCE)[0], 15.0)

    def test_the_accent_is_never_confusable_with_a_data_colour(self) -> None:
        signal = TOKENS["color"]["accent"]["signal"]
        for colour in group_colors():
            self.assertGreaterEqual(delta_e(signal, colour), 15.0, colour)
            for vision in ("protan", "deutan"):
                self.assertGreaterEqual(delta_e(signal, colour, vision), 8.0, f"{colour} {vision}")

    def test_status_colours_are_not_data_colours(self) -> None:
        self.assertFalse(set(TOKENS["color"]["status"].values()) & set(group_colors()))


class KindEncodingTest(unittest.TestCase):
    def test_every_entity_kind_has_exactly_one_visual_encoding(self) -> None:
        self.assertEqual(set(TOKENS["data"]["kinds"]), {kind.value for kind in EntityKind})

    def test_kinds_reference_real_groups_and_shapes(self) -> None:
        for kind, spec in TOKENS["data"]["kinds"].items():
            self.assertIn(spec["group"], TOKENS["data"]["groups"], kind)
            self.assertIn(spec["shape"], TOKENS["data"]["shapes"], kind)

    def test_group_and_shape_pair_is_unique_per_kind(self) -> None:
        pairs = [(s["group"], s["shape"]) for s in TOKENS["data"]["kinds"].values()]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_every_group_is_used_by_some_kind(self) -> None:
        used = {s["group"] for s in TOKENS["data"]["kinds"].values()}
        self.assertEqual(used, set(TOKENS["data"]["groups"]))

    def test_every_shape_is_used(self) -> None:
        used = {s["shape"] for s in TOKENS["data"]["kinds"].values()}
        self.assertEqual(used, set(TOKENS["data"]["shapes"]))

    def test_labels_are_unique_and_human_readable(self) -> None:
        labels = [s["label"] for s in TOKENS["data"]["kinds"].values()]
        self.assertEqual(len(labels), len(set(labels)))
        for label in labels:
            self.assertEqual(label, label.strip())
            self.assertNotIn("_", label)

    def test_no_shape_repeats_inside_a_group(self) -> None:
        by_group: dict[str, list[str]] = {}
        for spec in TOKENS["data"]["kinds"].values():
            by_group.setdefault(spec["group"], []).append(spec["shape"])
        for group, shapes in by_group.items():
            self.assertEqual(len(shapes), len(set(shapes)), group)


class ProvenanceEncodingTest(unittest.TestCase):
    def test_every_provenance_label_is_encoded(self) -> None:
        self.assertEqual(set(TOKENS["provenance"]["kinds"]), {p.value for p in Provenance})

    def test_each_label_points_at_a_defined_style(self) -> None:
        for name, spec in TOKENS["provenance"]["kinds"].items():
            self.assertIn(spec["style"], TOKENS["provenance"]["styles"], name)

    def test_line_styles_are_visually_distinct(self) -> None:
        styles = TOKENS["provenance"]["styles"]
        dashes = [s["dash"] for s in styles.values()]
        outlines = [s["outline"] for s in styles.values()]
        self.assertEqual(len(dashes), len(set(dashes)))
        self.assertEqual(len(outlines), len(set(outlines)))
        self.assertEqual(styles["verified"]["dash"], "none")

    def test_only_sourced_is_drawn_as_verified(self) -> None:
        verified = [k for k, s in TOKENS["provenance"]["kinds"].items() if s["style"] == "verified"]
        self.assertEqual(verified, ["sourced"])

    def test_ai_and_prediction_are_never_drawn_like_facts(self) -> None:
        kinds = TOKENS["provenance"]["kinds"]
        self.assertEqual(kinds["ai_generated"]["style"], "conjecture")
        self.assertEqual(kinds["predicted"]["style"], "conjecture")

    def test_seed_data_is_labelled_as_development_data(self) -> None:
        self.assertIn("seed", TOKENS["provenance"]["kinds"]["seed"]["label"].lower())

    def test_labels_and_icons_are_unique_per_provenance(self) -> None:
        kinds = TOKENS["provenance"]["kinds"].values()
        self.assertEqual(len({k["label"] for k in kinds}), len(TOKENS["provenance"]["kinds"]))
        self.assertEqual(len({k["icon"] for k in kinds}), len(TOKENS["provenance"]["kinds"]))

    def test_dash_patterns_are_valid_svg_dasharrays(self) -> None:
        for style in TOKENS["provenance"]["styles"].values():
            self.assertRegex(style["dash"], r"^(none|\d+( \d+)*)$")


class TypographyTest(unittest.TestCase):
    def test_roles_use_declared_families(self) -> None:
        for role, spec in TOKENS["type"]["roles"].items():
            self.assertIn(spec["family"], TOKENS["type"]["families"], role)

    def test_text_is_never_smaller_than_13px(self) -> None:
        for role, spec in TOKENS["type"]["roles"].items():
            size = float(spec["size"].removesuffix("rem")) * 16
            self.assertGreaterEqual(size, 13.0, role)

    def test_reading_text_has_a_comfortable_measure_and_leading(self) -> None:
        reading = TOKENS["type"]["roles"]["reading"]
        self.assertLessEqual(reading["maxCh"], 75)
        self.assertGreaterEqual(reading["line"], 1.5)
        self.assertEqual(reading["family"], "serif")
        self.assertEqual(TOKENS["layout"]["readingMeasure"], f"{reading['maxCh']}ch")

    def test_figures_use_tabular_numerals(self) -> None:
        self.assertEqual(TOKENS["type"]["roles"]["figure"]["features"], "tnum")

    def test_monospace_is_a_system_stack_and_only_for_code(self) -> None:
        self.assertNotIn("code", [s["family"] for r, s in TOKENS["type"]["roles"].items() if r != "code"])
        declared = {font["family"] for font in TOKENS["type"]["webFonts"]}
        self.assertEqual(declared, {"Newsreader", "Hanken Grotesk"})

    def test_web_fonts_are_open_licensed(self) -> None:
        for font in TOKENS["type"]["webFonts"]:
            self.assertEqual(font["license"], "OFL-1.1", font["family"])


class MotionAndLayoutTest(unittest.TestCase):
    def test_durations_are_short_and_ordered(self) -> None:
        durations = {k: float(v.removesuffix("ms")) for k, v in TOKENS["motion"]["duration"].items()}
        self.assertEqual(durations["instant"], 0)
        ordered = [durations[k] for k in ("instant", "quick", "base", "slow", "camera")]
        self.assertEqual(ordered, sorted(ordered))
        self.assertLessEqual(max(ordered), 600)

    def test_reduced_motion_zeroes_every_duration(self) -> None:
        css = render_css(TOKENS)
        block = css.split("@media (prefers-reduced-motion: reduce)")[1].split("}\n}")[0]
        for name in TOKENS["motion"]["duration"]:
            self.assertRegex(block, rf"--vy-motion-{name}: 0ms;")

    def test_coarse_pointers_get_larger_targets(self) -> None:
        self.assertGreaterEqual(px(TOKENS["target"]["coarsePointer"]), 44)
        self.assertGreaterEqual(px(TOKENS["target"]["minimum"]), 24)
        self.assertGreaterEqual(px(TOKENS["target"]["finePointer"]), px(TOKENS["target"]["minimum"]))
        self.assertIn("(pointer: coarse)", render_css(TOKENS))

    def test_breakpoints_ascend(self) -> None:
        values = [px(v) if v != "0" else 0 for v in TOKENS["breakpoint"].values()]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(values), len(set(values)))

    def test_the_canvas_keeps_a_usable_width_at_each_docked_breakpoint(self) -> None:
        layout, bp = TOKENS["layout"], TOKENS["breakpoint"]
        expanded = px(bp["expanded"]) - px(layout["railCollapsedWidth"]) - px(layout["dossierWidth"])
        wide = px(bp["wide"]) - px(layout["railWidth"]) - px(layout["dossierWidth"])
        self.assertGreaterEqual(expanded, 600)
        self.assertGreaterEqual(wide, 700)

    def test_layers_are_strictly_increasing(self) -> None:
        values = list(TOKENS["layer"].values())
        self.assertEqual(values, sorted(set(values)))

    def test_focus_ring_is_visible_and_separated(self) -> None:
        self.assertGreaterEqual(px(TOKENS["border"]["focus"]), 2)
        self.assertGreaterEqual(px(TOKENS["border"]["focusGap"]), 2)

    def test_budgets_match_the_api_limits(self) -> None:
        self.assertEqual(TOKENS["budget"]["maxNodesFromApi"], limits.MAX_GRAPH_NODES)
        self.assertEqual(TOKENS["budget"]["maxEdgesFromApi"], limits.MAX_GRAPH_EDGES)
        self.assertIn("Nothing here has been measured", TOKENS["budget"]["note"])


class GeneratedOutputTest(unittest.TestCase):
    def test_css_is_current(self) -> None:
        path = ROOT / "apps" / "web" / "src" / "styles" / "tokens.css"
        self.assertEqual(path.read_text(encoding="utf-8"), render_css(TOKENS), "run scripts/generate_design_tokens.py")

    def test_typescript_is_current(self) -> None:
        path = ROOT / "packages" / "ui" / "src" / "tokens.ts"
        self.assertEqual(
            path.read_text(encoding="utf-8"), render_typescript(TOKENS), "run scripts/generate_design_tokens.py"
        )

    def test_css_custom_property_names_are_unique_and_prefixed(self) -> None:
        names = re.findall(r"^\s+(--[a-z0-9-]+):", render_css(TOKENS).split("@media")[0], flags=re.M)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(n.startswith("--vy-") for n in names))

    def test_kebab_casing(self) -> None:
        self.assertEqual(kebab("dossierWidth"), "dossier-width")
        self.assertEqual(kebab("ai_model"), "ai-model")
        self.assertEqual(kebab("signal-hover"), "signal-hover")
        self.assertEqual(kebab("railCollapsedWidth"), "rail-collapsed-width")


if __name__ == "__main__":
    unittest.main()
