#include "Decisions.h"

#include <set>
#include <sstream>
#include <utility>

namespace kcfg {

void includeHits(Hits &all, Hits const next) {
    all.boot |= next.boot;
    all.workload |= next.workload;
    all.init |= next.init;
}

std::string_view settingText(Setting const value) {
    switch (value) {
    case Setting::absent: return "absent";
    case Setting::no: return "n";
    case Setting::module: return "m";
    case Setting::yes: return "y";
    case Setting::other: return "non-tristate";
    }
    std::unreachable();
}

Proposal automaticChoice(Row const &row, bool const modulesEnabled, bool const tristate) {
    auto keep = [&](std::string_view why) { return Proposal{row.before, why}; };
    auto const &e = row.evidence;
    if (not row.policy) return keep("outside observation scope");
    if (row.symbol == "CONFIG_MODULES") return keep("module infrastructure");
    if (row.policy == Policy::keep) return keep("protected by board policy");
    if (e.covered != e.total) return keep("incomplete instrumentation coverage");
    if (not row.editable or (row.before != Setting::yes and row.before != Setting::module))
        return keep("baseline is not enabled or not tristate");
    if (e.hits.boot) return keep("used before workload");
    if (row.policy == Policy::drop) {
        if (e.hits.workload) return keep("workload used this symbol");
        return {Setting::no, "drop permitted; no boot or workload hit"};
    }
    if (not modulesEnabled) return keep("module support is not built in");
    if (not tristate or row.module == "-") return keep("no audited loadable module");
    return {Setting::module, "defer permitted; no pre-workload use", true};
}

Bundle materialize(Campaign const &campaign, std::span<Row const> const rows) {
    auto lines = campaign.baseline.lines;
    std::ostringstream changes, overrides, report, modules;
    report << "symbol\tbefore\trequested\tboot\tworkload\tinit\tcovered_units\ttotal_units\treason\n";
    std::set<std::string> late;
    for (auto const &row : rows) {
        bool const manual = row.authority == Authority::manual;
        bool const explicitValue = row.locked or manual;
        bool const changed = row.before != row.current;
        if (not explicitValue and not row.policy and not changed) continue;
        auto const old = settingText(row.before), value = settingText(row.current);
        if (changed) changes << row.symbol << '\t' << old << '\t' << value << '\n';
        if (explicitValue)
            overrides << row.symbol << '\t' << value << '\t' << (manual ? "manual" : "forced") << '\n';
        auto const entry = campaign.baseline.entries.find(row.symbol);
        if (changed or (explicitValue and entry == campaign.baseline.entries.end())) {
            auto line = row.current == Setting::no ? "# " + row.symbol + " is not set" : row.symbol + "=" + std::string{value};
            if (entry == campaign.baseline.entries.end()) lines.push_back(std::move(line));
            else lines[entry->second.line] = std::move(line);
        }
        if (row.late) late.insert(row.module);
        auto const &e = row.evidence;
        report << row.symbol << '\t' << old << '\t' << value << '\t' << e.hits.boot << '\t' << e.hits.workload
               << '\t' << e.hits.init << '\t' << e.covered << '\t' << e.total << '\t' << row.reason << '\n';
    }
    std::string config;
    for (std::size_t i{}; i < lines.size(); ++i) {
        config += lines[i];
        if (i + 1 < lines.size() or campaign.baseline.trailingNewline) config += '\n';
    }
    for (auto const &module : late) modules << module << '\n';
    return {.identity = campaign.id + '\n', .config = std::move(config), .changes = std::move(changes).str(),
            .lateModules = std::move(modules).str(), .report = std::move(report).str(), .overrides = std::move(overrides).str()};
}

} // namespace kcfg
