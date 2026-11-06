#include <catch2/catch_all.hpp>

#include <boost/filesystem.hpp>

#include <nlohmann/json.hpp>

#include <cctype>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <string>

#include "libslic3r/PresetBundle.hpp"
#include "libslic3r/PrintConfig.hpp"

using namespace Slic3r;

namespace {

namespace fs = boost::filesystem;

static const char *g_bbl_gcode_keys[] = {
    "machine_start_gcode", "machine_end_gcode", "change_filament_gcode",
    "time_lapse_gcode", "layer_change_gcode", "wrapping_detection_gcode"
};

const std::set<std::string> &parser_keywords() {
    static const std::set<std::string> keywords = {
        "and", "digits", "zdigits", "empty", "if", "int", "is_nil", "local",
        "else", "elsif", "endif", "false", "global", "interpolate_table",
        "min", "max", "random", "regex_replace", "filament_change", "repeat",
        "round", "floor", "ceil", "not", "one_of", "or", "size", "true"
    };
    return keywords;
}

std::string read_file(const fs::path &path) {
    std::ifstream in(path.string(), std::ios::binary);
    std::stringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

std::set<std::string> extract_identifiers(const std::string &gcode, const std::set<std::string> &keywords) {
    static const std::regex brace_block_re("\\{([^{}]*)\\}");
    static const std::regex string_literal_re("\"[^\"]*\"");
    static const std::regex local_decl_re("\\b(?:local|global)\\s+([A-Za-z_]\\w*)");
    static const std::regex identifier_re("[A-Za-z_]\\w*");
    static const std::regex bracket_re("\\[([A-Za-z_]\\w*)\\]");

    std::set<std::string> result;
    std::set<std::string> locals;
    std::string            outside = gcode;

    for (std::sregex_iterator it(gcode.begin(), gcode.end(), brace_block_re), end; it != end; ++it) {
        const std::string block = (*it)[1].str();

        for (std::sregex_iterator lit(block.begin(), block.end(), local_decl_re), lend; lit != lend; ++lit)
            locals.insert((*lit)[1].str());

        const std::string blanked = std::regex_replace(block, string_literal_re, "");

        for (std::sregex_iterator tit(blanked.begin(), blanked.end(), identifier_re), tend; tit != tend; ++tit) {
            const auto pos = tit->position(0);
            const char prev = pos > 0 ? blanked[static_cast<size_t>(pos) - 1] : '\0';
            if (std::isalnum(static_cast<unsigned char>(prev)) || prev == '_' || prev == '.')
                continue;
            result.insert(tit->str());
        }

        const size_t block_start = static_cast<size_t>(it->position(0));
        const size_t block_len = static_cast<size_t>(it->length(0));
        outside.replace(block_start, block_len, std::string(block_len, ' '));
    }

    for (std::sregex_iterator bit(outside.begin(), outside.end(), bracket_re), bend; bit != bend; ++bit)
        result.insert((*bit)[1].str());

    for (const std::string &loc : locals)
        result.erase(loc);
    for (const std::string &kw : keywords)
        result.erase(kw);
    result.erase("true");
    result.erase("false");

    return result;
}

std::set<std::string> known_identifiers(const fs::path &libslic3r_dir) {
    static const std::regex literal_re("\"([A-Za-z_][A-Za-z0-9_]*)\"");
    std::set<std::string>   result;

    auto scan_file = [&](const fs::path &file) {
        if (!fs::exists(file))
            return;
        const std::string text = read_file(file);
        for (std::sregex_iterator it(text.begin(), text.end(), literal_re), end; it != end; ++it)
            result.insert((*it)[1].str());
    };

    scan_file(libslic3r_dir / "PrintConfig.cpp");
    scan_file(libslic3r_dir / "PlaceholderParser.cpp");
    scan_file(libslic3r_dir / "Print.cpp");
    scan_file(libslic3r_dir / "GCode.cpp");

    const fs::path gcode_dir = libslic3r_dir / "GCode";
    if (fs::exists(gcode_dir) && fs::is_directory(gcode_dir))
        for (fs::directory_iterator dit(gcode_dir), dend; dit != dend; ++dit)
            if (fs::is_regular_file(dit->path()) && dit->path().extension() == ".cpp")
                scan_file(dit->path());

    return result;
}

bool is_known_identifier(const std::string &id, const std::set<std::string> &source_identifiers) {
    return print_config_def.has(id) || custom_gcode_specific_config_def.has(id)
        || source_identifiers.count(id) > 0;
}

struct BblBundleLoad {
    PresetBundle               base_bundle;
    PresetBundle               bundle;
    PresetsConfigSubstitutions substitutions;
    size_t                     presets_loaded = 0;
};

BblBundleLoad &bbl_load() {
    static BblBundleLoad load;
    static bool          initialized = false;
    if (!initialized) {
        load.base_bundle.load_vendor_configs_from_json(
            std::string(PROFILES_DIR), PresetBundle::ORCA_FILAMENT_LIBRARY,
            PresetBundle::LoadSystem, ForwardCompatibilitySubstitutionRule::Disable);
        const auto result = load.bundle.load_vendor_configs_from_json(
            std::string(PROFILES_DIR), "BBL", PresetBundle::LoadSystem,
            ForwardCompatibilitySubstitutionRule::Disable, &load.base_bundle);
        load.substitutions = result.first;
        load.presets_loaded = result.second;
        initialized = true;
    }
    return load;
}

const PresetBundle &bbl_bundle() {
    return bbl_load().bundle;
}

}

TEST_CASE("BBL system profiles load with no errors and no substitutions", "[BblProfiles]") {
    REQUIRE_NOTHROW(bbl_load());
    const BblBundleLoad &load = bbl_load();
    REQUIRE(load.substitutions.empty());
    REQUIRE(load.presets_loaded > 0);
}

TEST_CASE("No BBL machine profile carries an include key", "[BblProfiles]") {
    const fs::path machine_dir = fs::path(PROFILES_DIR) / "BBL" / "machine";
    REQUIRE(fs::exists(machine_dir));

    for (fs::directory_iterator it(machine_dir), end; it != end; ++it) {
        if (!fs::is_regular_file(it->path()) || it->path().extension() != ".json")
            continue;

        DYNAMIC_SECTION(it->path().filename().string()) {
            const nlohmann::json j = nlohmann::json::parse(read_file(it->path()));
            CHECK_FALSE(j.contains("include"));
        }
    }
}

TEST_CASE("BBL machine gcode references only known identifiers", "[BblProfiles]") {
    const PresetBundle   &bundle = bbl_bundle();
    const fs::path         libslic3r_dir = fs::path(PROFILES_DIR) / ".." / ".." / "src" / "libslic3r";
    const std::set<std::string> source_identifiers = known_identifiers(libslic3r_dir);

    for (auto it = bundle.printers.begin(); it != bundle.printers.end(); ++it) {
        const Preset &preset = *it;
        if (!preset.is_system)
            continue;
        if (preset.vendor == nullptr || preset.vendor->id != "BBL")
            continue;

        for (const char *key : g_bbl_gcode_keys) {
            if (!preset.config.has(key))
                continue;
            const std::string gcode = preset.config.opt_string(key);
            if (gcode.empty())
                continue;

            DYNAMIC_SECTION(preset.name << " " << key) {
                const std::set<std::string> ids = extract_identifiers(gcode, parser_keywords());
                for (const std::string &id : ids) {
                    INFO("preset=" << preset.name << " key=" << key << " identifier=" << id);
                    CHECK(is_known_identifier(id, source_identifiers));
                }
            }
        }
    }
}

TEST_CASE("Identifier scan ignores locals and strings but reports unknown names", "[BblProfiles]") {
    const std::string gcode = "{local t = 1e3}{if curr_bed_type == \"x\" and t > 0}[bogus_name_xyz]{endif}";
    const std::set<std::string> ids = extract_identifiers(gcode, parser_keywords());
    REQUIRE(ids == std::set<std::string>{"curr_bed_type", "bogus_name_xyz"});

    const fs::path libslic3r_dir = fs::path(PROFILES_DIR) / ".." / ".." / "src" / "libslic3r";
    const std::set<std::string> source_identifiers = known_identifiers(libslic3r_dir);
    CHECK_FALSE(is_known_identifier("bogus_name_xyz", source_identifiers));
}
