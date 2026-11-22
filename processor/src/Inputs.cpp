#include "Identity.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <cstdint>
#include <set>
#include <utility>

namespace kcfg {
namespace {

using Text = std::string_view;
constexpr unsigned kMaxId = 65535;

struct Lines {
    explicit Lines(Text const text) : remaining{text} {}

    std::optional<Text> next() {
        if (remaining.empty()) return std::nullopt;
        auto const end = remaining.find('\n');
        auto const line = remaining.substr(0, end);
        remaining.remove_prefix(end == Text::npos ? remaining.size() : end + 1);
        ++number;
        return line;
    }

    std::unexpected<std::string> error(Text const description) const {
        return std::unexpected{"line " + std::to_string(number) + ": " + std::string{description}};
    }

private:
    Text remaining;
    std::size_t number{};
};

template<std::size_t N>
std::optional<std::array<Text, N>> fields(Text rest) {
    std::array<Text, N> values{};
    for (auto &value : values) {
        auto const end = rest.find('\t');
        if ((end == Text::npos) != (&value == &values.back())) return std::nullopt;
        value = rest.substr(0, end);
        if (end != Text::npos) rest.remove_prefix(end + 1);
    }
    return values;
}

template<typename T>
std::optional<T> number(Text const field) {
    if (field.empty()) return std::nullopt;
    T value{};
    auto const end = field.data() + field.size();
    auto const parsed = std::from_chars(field.data(), end, value);
    if (parsed.ec != std::errc{} or parsed.ptr != end) return std::nullopt;
    return value;
}

bool alphanumeric(char const c) {
    return (c >= 'a' and c <= 'z') or (c >= 'A' and c <= 'Z') or (c >= '0' and c <= '9');
}

bool identifierChar(char const c) { return alphanumeric(c) or c == '_'; }

bool symbolName(Text const name) {
    return name.starts_with("CONFIG_") and name.size() > 7
           and std::ranges::all_of(name.substr(7), identifierChar);
}

bool moduleName(Text const name) {
    if (name == "-") return true;
    return not name.empty() and identifierChar(name.front())
           and std::ranges::all_of(name, [](char const c) { return identifierChar(c) or c == '-'; });
}

bool sourcePath(Text path) {
    if (not path.ends_with(".c") or path.size() > 4095) return false;
    while (not path.empty()) {
        auto const slash = path.find('/');
        auto const part = path.substr(0, slash);
        if (part.empty() or part == "." or part == "..") return false;
        if (not std::ranges::all_of(part, [](char const c) {
                return identifierChar(c) or c == '-' or c == '+' or c == '.';
            })) return false;
        if (slash == Text::npos) return true;
        path.remove_prefix(slash + 1);
    }
    return false;
}

bool cleanText(Text const text) {
    return std::ranges::none_of(text, [](unsigned char const c) {
        return (c < 32 and c != '\n' and c != '\t') or c == 127;
    });
}

bool digestLine(Text const id) {
    return id.size() == 65 and id.back() == '\n' and
           std::ranges::all_of(id.substr(0, 64), [](char const c) {
               return (c >= '0' and c <= '9') or (c >= 'a' and c <= 'f');
           });
}

Setting setting(Text const value) {
    if (value == "y") return Setting::yes;
    if (value == "m") return Setting::module;
    if (value == "n") return Setting::no;
    return Setting::other;
}

Result<Baseline> parseBaseline(Text const text) {
    if (not cleanText(text)) return std::unexpected{"control character in baseline"};
    Baseline parsed{.lines = {}, .entries = {}, .trailingNewline = text.ends_with('\n')};
    auto lines = Lines{text};
    while (auto const row = lines.next()) {
        auto const index = parsed.lines.size();
        parsed.lines.emplace_back(*row);
        if (row->empty()) continue;
        auto name = Text{};
        auto value = Setting::no;
        if (row->starts_with('#')) {
            constexpr Text suffix{" is not set"};
            if (not row->starts_with("# CONFIG_") or not row->ends_with(suffix)) continue;
            name = row->substr(2, row->size() - 2 - suffix.size());
        } else {
            auto const eq = row->find('=');
            if (eq == Text::npos) return lines.error("invalid Kconfig assignment");
            name = row->substr(0, eq);
            value = setting(row->substr(eq + 1));
        }
        if (not symbolName(name)) return lines.error("invalid Kconfig symbol");
        if (not parsed.entries.emplace(name, Baseline::Entry{index, value}).second)
            return lines.error("duplicate Kconfig symbol");
    }
    if (parsed.entries.empty()) return std::unexpected{"no Kconfig entries"};
    return parsed;
}

std::optional<Policy> policy(Text const value) {
    if (value == "keep") return Policy::keep;
    if (value == "drop") return Policy::drop;
    if (value == "defer") return Policy::defer;
    return std::nullopt;
}

Result<std::vector<Unit>> parseScope(Text const text) {
    if (not cleanText(text)) return std::unexpected{"control character in scope"};
    std::set<Text> sources;
    std::map<std::string, Text> moduleOwners;
    std::map<Text, std::pair<Text, Policy>> policies;
    std::map<unsigned, Unit> indexed;
    auto lines = Lines{text};
    while (auto const row = lines.next()) {
        if (row->empty() or row->starts_with('#')) continue;
        auto const columns = fields<5>(*row);
        if (not columns) return lines.error("expected five tab-separated fields");
        auto const &[idText, symbol, module, rule, source] = *columns;
        auto const id = number<unsigned>(idText);
        auto const action = policy(rule);
        if (not id or *id == 0 or *id > kMaxId) return lines.error("ID outside 1..65535");
        if (not action or not symbolName(symbol) or not moduleName(module) or not sourcePath(source))
            return lines.error("invalid symbol, module, policy or C source path");
        if (not sources.emplace(source).second) return lines.error("duplicate source path");
        auto const group = std::pair{module, *action};
        if (policies.try_emplace(symbol, group).first->second != group)
            return lines.error("conflicting module or policy for one symbol");
        if (module != "-") {
            auto normalized = std::string{module};
            std::ranges::replace(normalized, '-', '_');
            if (moduleOwners.try_emplace(normalized, symbol).first->second != symbol)
                return lines.error("module name belongs to multiple symbols");
        }
        auto unit = Unit{.symbol = std::string{symbol}, .module = std::string{module}, .policy = *action};
        if (not indexed.emplace(*id, std::move(unit)).second) return lines.error("duplicate ID");
    }
    if (indexed.empty() or indexed.rbegin()->first != indexed.size())
        return std::unexpected{"expected nonempty, dense IDs starting at 1"};
    std::vector<Unit> units;
    units.reserve(indexed.size());
    for (auto &[id, unit] : indexed) units.push_back(std::move(unit));
    return units;
}

std::optional<std::size_t> indexOf(Text const field, std::size_t const size) {
    auto const id = number<unsigned>(field);
    if (not id or *id == 0 or *id > size) return std::nullopt;
    return *id - 1;
}

Result<void> addCoverage(std::vector<Unit> &units, Text const text) {
    auto seen = std::vector<bool>(units.size());
    auto lines = Lines{text};
    while (auto const row = lines.next()) {
        auto const columns = fields<2>(*row);
        if (not columns) return lines.error("expected ID and site count");
        auto const index = indexOf((*columns)[0], units.size());
        auto const sites = number<std::uint64_t>((*columns)[1]);
        if (not index or not sites) return lines.error("invalid ID or site count");
        if (seen[*index]) return lines.error("duplicate coverage ID");
        seen[*index] = true;
        units[*index].covered = *sites > 0;
    }
    return {};
}

std::optional<bool> bit(Text const value) {
    if (value == "0") return false;
    if (value == "1") return true;
    return std::nullopt;
}

} // namespace

Result<Campaign> parseCampaign(CampaignText const &inputs) {
    if (not digestLine(inputs.id)) return std::unexpected{"campaign.id: expected 64 lowercase hexadecimal digits and LF"};
    if (not digestLine(inputs.seed)) return std::unexpected{"campaign.seed: expected 64 lowercase hexadecimal digits and LF"};
    auto const computed = campaignDigest(inputs);
    if (not computed) return std::unexpected{computed.error()};
    if (*computed != inputs.id.substr(0, 64)) return std::unexpected{"campaign ID does not match the seed, scope and baseline"};
    auto baseline = parseBaseline(inputs.baseline);
    if (not baseline) return std::unexpected{"baseline.config: " + baseline.error()};
    auto units = parseScope(inputs.scope);
    if (not units) return std::unexpected{"scope.tsv: " + units.error()};
    auto const coverage = addCoverage(*units, inputs.coverage);
    if (not coverage) return std::unexpected{"coverage.tsv: " + coverage.error()};
    return Campaign{.id = std::string{inputs.id.substr(0, 64)}, .baseline = std::move(*baseline),
                    .units = std::move(*units)};
}

Result<std::vector<Hits>> parseTrace(Text const trace, Campaign const &campaign) {
    if (not trace.ends_with('\n')) return std::unexpected{"truncated trace: missing final LF"};
    auto lines = Lines{trace};
    if (lines.next() != "KCFG-TRACE\t1\t" + campaign.id)
        return std::unexpected{"wrong trace version or campaign ID"};
    auto const count = campaign.units.size();
    auto seen = std::vector<bool>(count);
    auto result = std::vector<Hits>(count);
    for (std::size_t n{}; n < count; ++n) {
        auto const row = lines.next();
        if (not row) return std::unexpected{"missing trace rows"};
        auto const columns = fields<4>(*row);
        if (not columns) return lines.error("expected ID and three hit flags");
        auto const index = indexOf((*columns)[0], count);
        auto const boot = bit((*columns)[1]);
        auto const work = bit((*columns)[2]);
        auto const init = bit((*columns)[3]);
        if (not index or not boot or not work or not init) return lines.error("invalid ID or flag");
        if (seen[*index]) return lines.error("duplicate trace ID");
        seen[*index] = true;
        result[*index] = {.boot = *boot, .workload = *work, .init = *init};
    }
    if (lines.next() != "END\t" + std::to_string(count) or lines.next().has_value())
        return std::unexpected{"invalid trace footer, row count or trailing data"};
    return result;
}

} // namespace kcfg
