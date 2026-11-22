#include <kcfg/Plan.h>

#include <cassert>
#include <set>
#include <sstream>
#include <utility>

namespace kcfg {
namespace {

struct Group {
    std::string module;
    Policy policy;
    Evidence evidence;
};

Setting original(Baseline const &baseline, std::string const &symbol) {
    auto const found = baseline.entries.find(symbol);
    return found == baseline.entries.end() ? Setting::absent : found->second.value;
}

void decide(Choice &choice, Policy const policy, bool const modulesEnabled) {
    auto const &e = choice.evidence;
    if (choice.symbol == "CONFIG_MODULES") choice.reason = "module infrastructure";
    else if (policy == Policy::keep) choice.reason = "protected by board policy";
    else if (e.covered != e.total) choice.reason = "incomplete instrumentation coverage";
    else if (choice.before != Setting::yes and choice.before != Setting::module)
        choice.reason = "baseline is not enabled or not tristate";
    else if (e.hits.boot) choice.reason = "used before workload";
    else if (policy == Policy::drop and e.hits.workload) choice.reason = "workload used this symbol";
    else if (policy == Policy::drop) {
        choice.reason = "drop permitted; no boot or workload hit";
        choice.after = Setting::no;
    } else if (not modulesEnabled) choice.reason = "module support is not built in";
    else if (choice.module == "-") choice.reason = "no audited loadable module";
    else {
        choice.reason = "defer permitted; no pre-workload use";
        choice.after = Setting::module;
        choice.loadLate = true;
    }
    choice.accepted = actionable(choice);
}

std::string changedLine(Choice const &choice) {
    return choice.after == Setting::no ? "# " + choice.symbol + " is not set"
                                       : choice.symbol + "=" + std::string{settingText(choice.after)};
}

} // namespace

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

bool actionable(Choice const &choice) { return choice.before != choice.after or choice.loadLate; }

std::vector<Choice> propose(Campaign const &campaign, std::vector<Hits> const &hits) {
    assert(campaign.units.size() == hits.size());
    std::map<std::string, Group> groups;
    for (std::size_t i{}; i < hits.size(); ++i) {
        auto const &unit = campaign.units[i];
        auto &e = groups.try_emplace(unit.symbol, Group{unit.module, unit.policy, {}}).first->second.evidence;
        includeHits(e.hits, hits[i]);
        ++e.total;
        if (unit.covered) ++e.covered;
    }
    auto const modulesEnabled = original(campaign.baseline, "CONFIG_MODULES") == Setting::yes;
    std::vector<Choice> choices;
    choices.reserve(groups.size());
    for (auto const &[symbol, group] : groups) {
        auto const value = original(campaign.baseline, symbol);
        auto choice = Choice{.symbol = symbol, .module = group.module, .evidence = group.evidence,
                             .before = value, .after = value, .reason = {}};
        decide(choice, group.policy, modulesEnabled);
        choices.push_back(std::move(choice));
    }
    return choices;
}

Bundle materialize(Campaign const &campaign, std::vector<Choice> const &choices) {
    auto const &baseline = campaign.baseline;
    auto lines = baseline.lines;
    std::ostringstream changes;
    std::ostringstream report;
    report << "symbol\tbefore\trequested\tboot\tworkload\tinit\tcovered_units\ttotal_units\treason\n";
    std::set<std::string> late;
    for (auto const &c : choices) {
        auto const accepted = actionable(c) and c.accepted;
        auto const after = settingText(accepted ? c.after : c.before);
        if (accepted and c.before != c.after) {
            lines.at(baseline.entries.at(c.symbol).line) = changedLine(c);
            changes << c.symbol << '\t' << settingText(c.before) << '\t' << after << '\n';
        }
        if (accepted and c.loadLate) late.insert(c.module);
        auto const &e = c.evidence;
        report << c.symbol << '\t' << settingText(c.before) << '\t' << after << '\t' << e.hits.boot
               << '\t' << e.hits.workload << '\t' << e.hits.init << '\t' << e.covered << '\t' << e.total
               << '\t' << (actionable(c) and not c.accepted ? "rejected in review" : c.reason) << '\n';
    }
    std::ostringstream modules;
    for (auto const &module : late) modules << module << '\n';
    std::string config;
    for (std::size_t i{}; i < lines.size(); ++i) {
        config += lines[i];
        if (i + 1 < lines.size() or baseline.trailingNewline) config += '\n';
    }
    return {.identity = campaign.id + '\n', .config = std::move(config), .changes = std::move(changes).str(),
            .lateModules = std::move(modules).str(), .report = std::move(report).str()};
}

} // namespace kcfg
