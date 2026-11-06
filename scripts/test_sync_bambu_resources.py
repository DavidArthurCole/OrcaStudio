from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("sync_bambu_resources", Path(__file__).with_name("sync_bambu_resources.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class ResolveIncludesTests(unittest.TestCase):
    def test_include_applies_in_order_and_own_keys_win(self):
        by_name = {
            "A tpl start": {"name": "A tpl start", "instantiation": "false", "machine_start_gcode": "S1"},
            "A tpl start2": {"name": "A tpl start2", "instantiation": "false", "machine_start_gcode": "S2"},
            "A tpl end": {"name": "A tpl end", "instantiation": "false", "machine_end_gcode": "E1"},
        }
        doc = {"name": "A", "include": ["A tpl start", "A tpl start2", "A tpl end"], "machine_end_gcode": "OWN"}
        flat = m.resolve_includes(doc, by_name, "A.json")
        self.assertNotIn("include", flat)
        self.assertEqual(flat["machine_start_gcode"], "S2")
        self.assertEqual(flat["machine_end_gcode"], "OWN")

    def test_include_skips_empty_template_value(self):
        by_name = {"T": {"name": "T", "instantiation": "false", "layer_change_gcode": ""}}
        flat = m.resolve_includes({"name": "A", "include": ["T"]}, by_name, "A.json")
        self.assertNotIn("layer_change_gcode", flat)

    def test_missing_include_names_file_and_include(self):
        with self.assertRaises(m.SyncError) as cm:
            m.resolve_includes({"name": "A", "include": ["Nope"]}, {}, "A.json")
        self.assertIn("A.json", str(cm.exception))
        self.assertIn("Nope", str(cm.exception))

    def test_template_with_non_gcode_key_is_rejected(self):
        by_name = {"T": {"name": "T", "instantiation": "false", "retraction_length": ["1"]}}
        with self.assertRaises(m.SyncError):
            m.resolve_includes({"name": "A", "include": ["T"]}, by_name, "A.json")


class MergeGcodeTests(unittest.TestCase):
    def test_merge_gcode_overwrites_only_gcode_and_keeps_order(self):
        ours = {"name": "A", "support_air_filtration": "1", "machine_start_gcode": "old", "z_hop": ["0.4"]}
        flat = {"name": "A", "machine_start_gcode": "new", "wrapping_detection_gcode": "W", "z_hop": ["9"]}
        merged, changed = m.merge_gcode(ours, flat)
        self.assertEqual(list(merged), ["name", "support_air_filtration", "machine_start_gcode", "z_hop", "wrapping_detection_gcode"])
        self.assertEqual(merged["z_hop"], ["0.4"])
        self.assertEqual(changed, ["machine_start_gcode", "wrapping_detection_gcode"])


class IoHelperTests(unittest.TestCase):
    def test_unchanged_file_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.json"
            path.write_bytes(b'{\n\t"name": "A",\n\t"x": [\n\t\t"1"\n\t]\n}\n')
            before = path.read_bytes()
            self.assertFalse(m.write_if_changed(path, {"name": "A", "x": ["1"]}, False))
            self.assertEqual(path.read_bytes(), before)

    def test_dump_doc_uses_tabs_and_trailing_newline(self):
        self.assertEqual(m.dump_doc({"a": ["1"]}), '{\n\t"a": [\n\t\t"1"\n\t]\n}\n')


class ExtractIdentifiersTests(unittest.TestCase):
    def test_identifiers_ignore_locals_strings_keywords_and_numbers(self):
        g = '{local t = 1e3}{if curr_bed_type == "Textured PEI Plate" and t > 0}M104 S[nozzle_temperature_initial_layer]{endif}'
        ids = m.extract_identifiers(g, {"if", "endif", "and", "local"})
        self.assertEqual(ids, {"curr_bed_type", "nozzle_temperature_initial_layer"})


class MergeIndexTests(unittest.TestCase):
    def test_merge_index_appends_new_machines_and_skips_templates(self):
        ours = {"version": "1", "machine_model_list": [{"name": "M1", "sub_path": "machine/M1.json"}], "machine_list": [{"name": "A", "sub_path": "machine/A.json"}]}
        theirs = {"version": "2", "machine_model_list": [{"name": "M1", "sub_path": "machine/M1.json"}, {"name": "M2", "sub_path": "machine/M2.json"}],
                  "machine_list": [{"name": "A", "sub_path": "machine/A.json"}, {"name": "T", "sub_path": "machine/T.json"}, {"name": "B", "sub_path": "machine/B.json"}]}
        merged, added = m.merge_index(ours, theirs, {"T"})
        self.assertEqual(merged["version"], "2")
        self.assertEqual([e["name"] for e in merged["machine_list"]], ["A", "B"])
        self.assertEqual([e["name"] for e in merged["machine_model_list"]], ["M1", "M2"])
        self.assertEqual(added, ["M2", "B"])


class ValidateTests(unittest.TestCase):
    def test_validate_reports_unknown_key_and_unknown_placeholder(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            libslic3r = repo / "src" / "libslic3r"
            libslic3r.mkdir(parents=True)
            (libslic3r / "PrintConfig.cpp").write_text('def = this->add("machine_start_gcode", coString);\n', encoding="utf-8")
            (libslic3r / "Preset.hpp").write_text('#define BBL_JSON_KEY_NAME "name"\n', encoding="utf-8")
            (libslic3r / "PlaceholderParser.cpp").write_text('keywords.add("if")("endif");\n', encoding="utf-8")
            (libslic3r / "GCode.cpp").write_text("", encoding="utf-8")
            (libslic3r / "Print.cpp").write_text("", encoding="utf-8")
            offenders = m.validate(
                {"N.json": {"name": "N", "bogus_key": 1}},
                {"N.json:machine_start_gcode": "{if machine_start_gcode}{nope}{endif}"},
                repo,
            )
            self.assertEqual(len(offenders), 2)
            self.assertTrue(any("bogus_key" in o for o in offenders))
            self.assertTrue(any("nope" in o for o in offenders))


if __name__ == "__main__":
    unittest.main()
