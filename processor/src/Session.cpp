#include "Decisions.h"

#include <algorithm>
#include <utility>

namespace kcfg {
namespace {

struct Draft {
    Opinions manual;
    bool analyzed{};
    bool operator==(Draft const &) const = default;
};

struct ManualEdit {
    std::string symbol;
    std::optional<Setting> previous;
};

Result<void> checkSetting(DependencyGraph const &graph, std::string const &name, Setting value) {
    auto const type = graph.symbols().find(name);
    if (type == graph.symbols().end()) return std::unexpected{name + ": not a declared boolean or tristate option"};
    if (value == Setting::module and not type->second) return std::unexpected{name + ": boolean option cannot be m"};
    if (value != Setting::module and value != Setting::yes and value != Setting::no)
        return std::unexpected{name + ": expected y, m or n"};
    return {};
}

Result<std::vector<Row>> makeRows(Campaign const &campaign, std::span<Hits const> hits, DependencyGraph const &graph) {
    if (hits.size() != campaign.units.size()) return std::unexpected{"trace and scope sizes differ"};
    std::map<std::string, Row> indexed;
    for (auto const &[name, tristate] : graph.symbols()) {
        auto &row = indexed[name];
        row.before = Setting::no; // A switch omitted from .config has effective value n.
        row.editable = true;
    }
    for (auto const &[name, entry] : campaign.baseline.entries) {
        auto &row = indexed[name];
        if (row.editable != (entry.value != Setting::other))
            return std::unexpected{name + ": dependency export and baseline disagree on switch declaration"};
        row.before = entry.value;
        if (entry.value == Setting::other) {
            auto const &line = campaign.baseline.lines[entry.line];
            row.originalText = line.substr(line.find('=') + 1);
        }
    }
    for (std::size_t i{}; i < hits.size(); ++i) {
        auto const &unit = campaign.units[i];
        auto &row = indexed[unit.symbol];
        row.policy = unit.policy;
        row.module = unit.module;
        includeHits(row.evidence.hits, hits[i]);
        ++row.evidence.total;
        if (unit.covered) ++row.evidence.covered;
    }
    std::vector<Row> rows;
    rows.reserve(indexed.size());
    for (auto &[name, row] : indexed) {
        row.symbol = name;
        if (row.before != Setting::other) row.originalText = settingText(row.before);
        rows.push_back(std::move(row));
    }
    return rows;
}

struct SessionImpl final : Session {
    SessionImpl(Campaign const &campaign_, DependencyGraph const &graph_, Opinions forced_, std::vector<Row> rows_)
        : campaign{campaign_}, graph{graph_}, forced{std::move(forced_)}, table{std::move(rows_)} {
        for (std::size_t i{}; i < table.size(); ++i) index.emplace(table[i].symbol, i);
        refresh();
    }

    std::vector<Row> const &rows() const override { return table; }
    std::vector<Conflict> conflicts() const override { return problems; }
    bool dirty() const override { return draft != saved; }
    void markSaved() override { saved = draft; }

    Result<void> edit(std::string const &name, Setting value) override {
        if (auto ok = unlocked(name); not ok) return ok;
        if (auto ok = checkSetting(graph, name, value); not ok) return ok;
        auto const it = draft.manual.find(name);
        if (it != draft.manual.end() and it->second == value) return {};
        history.emplace_back(ManualEdit{name, it == draft.manual.end() ? std::nullopt : std::optional{it->second}});
        draft.manual[name] = value;
        refresh();
        return {};
    }

    Result<void> reset(std::string const &name) override {
        if (auto ok = unlocked(name); not ok) return ok;
        auto const it = draft.manual.find(name);
        if (it == draft.manual.end()) return std::unexpected{name + ": no manual choice to release"};
        history.emplace_back(ManualEdit{name, it->second});
        draft.manual.erase(it);
        refresh();
        return {};
    }

    Result<void> analyze() override {
        if (not draft.analyzed) {
            history.emplace_back(std::nullopt);
            draft.analyzed = true;
            refresh();
        }
        return {};
    }

    Result<void> undo() override {
        if (history.empty()) return std::unexpected{"Nothing to undo"};
        auto last = std::move(history.back());
        history.pop_back();
        if (not last) draft.analyzed = false;
        else if (last->previous) draft.manual[last->symbol] = *last->previous;
        else draft.manual.erase(last->symbol);
        refresh();
        return {};
    }

    std::string draftText() const override {
        auto text = "KCFG-DRAFT\t1\t" + campaign.id + (draft.analyzed ? "\nanalysis=on\n" : "\nanalysis=off\n");
        for (auto const &[name, value] : draft.manual)
            text += name + "=" + std::string{settingText(value)} + '\n';
        return text;
    }

    Result<void> restoreDraft(std::string_view text) override {
        auto const header = "KCFG-DRAFT\t1\t" + campaign.id + '\n';
        if (not text.starts_with(header)) return std::unexpected{"wrong draft version or capture campaign"};
        text.remove_prefix(header.size());
        auto const newline = text.find('\n');
        auto const mode = text.substr(0, newline);
        if (newline == text.npos or (mode != "analysis=on" and mode != "analysis=off"))
            return std::unexpected{"invalid draft analysis mode"};
        auto manual = parseOpinions(text.substr(newline + 1));
        if (not manual) return std::unexpected{manual.error()};
        for (auto const &[name, _] : forced) manual->erase(name);
        for (auto const &[name, value] : *manual)
            if (auto ok = checkSetting(graph, name, value); not ok) return ok;
        draft = {.manual = std::move(*manual), .analyzed = mode == "analysis=on"};
        history.clear();
        refresh();
        markSaved();
        return {};
    }

    Result<Bundle> application() const override {
        if (not problems.empty()) return std::unexpected{"cannot apply: " + problems.front().message};
        return materialize(campaign, table);
    }

private:
    Result<void> unlocked(std::string const &name) const {
        if (auto const it = forced.find(name); it != forced.end())
            return std::unexpected{name + " is locked by forced opinion at " + std::string{settingText(it->second)}};
        return {};
    }

    void refresh() {
        Values values;
        for (auto &row : table) {
            row.current = row.before;
            row.authority = Authority::baseline;
            row.locked = forced.contains(row.symbol);
            row.late = false;
            if (row.locked or draft.manual.contains(row.symbol)) {
                row.current = (row.locked ? forced : draft.manual).at(row.symbol);
                row.authority = row.locked ? Authority::forced : Authority::manual;
                row.reason = row.locked ? "locked by forced opinion" : "manual choice";
                if (row.current == Setting::no and (row.evidence.hits.boot or row.evidence.hits.workload))
                    row.reason += "; overrides recorded use";
            } else row.reason = row.policy ? "not analyzed" : "outside observation scope";
            if (row.editable and row.current != Setting::no) values[row.symbol] = row.current;
        }
        if (draft.analyzed) {
            auto const modules = values.find("CONFIG_MODULES");
            bool const enabled = modules != values.end() and modules->second == Setting::yes;
            for (auto &row : table) {
                if (not row.policy or row.authority != Authority::baseline) continue;
                auto const type = graph.symbols().find(row.symbol);
                auto const decision = automaticChoice(row, enabled, type != graph.symbols().end() and type->second);
                row.reason = decision.reason;
                if (not decision.late and decision.value == row.before) continue;
                row.current = decision.value;
                row.late = decision.late;
                row.authority = Authority::automatic;
                values[row.symbol] = decision.value;
            }
        }
        settle(values);
    }

    void settle(Values &values) {
        struct Withdrawal {
            Setting proposal;
            bool late;
            std::string reason;
            std::vector<std::string> participants;
        };
        std::map<std::size_t, Withdrawal> held;
        // Test the joint proposal first: a dependent and its prerequisite can often be dropped together.
        while (true) {
            problems = graph.conflicts(values);
            std::map<std::size_t, Conflict const *> withdrawals;
            for (auto const &problem : problems) {
                for (auto const &symbol : problem.symbols) {
                    auto const i = index.find(symbol);
                    if (i == index.end()) continue;
                    auto const &row = table[i->second];
                    if (row.authority != Authority::automatic or row.current == row.before) continue;
                    withdrawals.try_emplace(i->second, &problem);
                    break;
                }
            }
            if (withdrawals.empty()) break;
            for (auto const &[i, issue] : withdrawals) {
                auto &row = table[i];
                held.emplace(i, Withdrawal{row.current, row.late, std::move(row.reason), issue->symbols});
                row.authority = Authority::baseline;
                row.current = row.before;
                row.late = false;
                row.reason = "retained: " + issue->message;
                values[row.symbol] = row.before;
            }
        }
        if (not problems.empty()) return;
        // Restoring an alternative may have made an earlier hold unnecessary (e.g. A && B || C).
        for (auto &[i, hold] : held) {
            auto &row = table[i];
            if (not std::ranges::any_of(hold.participants, [&](auto const &name) {
                    auto const it = index.find(name);
                    return it != index.end() and it->second != i and held.contains(it->second);
                })) continue;
            values[row.symbol] = hold.proposal;
            auto const conflicts = graph.conflicts(values);
            if (conflicts.empty()) {
                row.authority = Authority::automatic;
                row.current = hold.proposal;
                row.late = hold.late;
                row.reason = std::move(hold.reason);
            } else {
                values[row.symbol] = row.before;
                row.reason = "retained: " + conflicts.front().message;
            }
        }
    }

    Campaign const &campaign;
    DependencyGraph const &graph;
    Opinions const forced;
    std::vector<Row> table;
    std::map<std::string, std::size_t> index;
    Draft draft, saved;
    std::vector<std::optional<ManualEdit>> history; // An empty entry undoes starting analysis.
    std::vector<Conflict> problems;
};

} // namespace

Result<std::unique_ptr<Session>> openSession(Campaign const &campaign, std::span<Hits const> hits,
                                             DependencyGraph const &graph, Opinions forced) {
    for (auto const &[name, setting] : forced)
        if (auto ok = checkSetting(graph, name, setting); not ok) return std::unexpected{"forced opinion: " + ok.error()};
    auto table = makeRows(campaign, hits, graph);
    if (not table) return std::unexpected{table.error()};
    return std::make_unique<SessionImpl>(campaign, graph, std::move(forced), std::move(*table));
}

} // namespace kcfg
