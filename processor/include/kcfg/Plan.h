#pragma once

#include <cstddef>
#include <expected>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace kcfg {

template<typename T>
using Result = std::expected<T, std::string>;

enum struct Setting { absent, no, module, yes, other };
enum struct Policy { keep, drop, defer };

struct Baseline {
    std::vector<std::string> lines;
    struct Entry {
        std::size_t line;
        Setting value;
    };
    std::map<std::string, Entry> entries;
    bool trailingNewline{};
};

struct Unit {
    std::string symbol;
    std::string module;
    Policy policy;
    bool covered{};
};

struct Hits {
    bool boot{};
    bool workload{};
    bool init{};
};

struct Campaign {
    std::string id;
    Baseline baseline;
    std::vector<Unit> units; // Indexed by wire ID minus one.
};

struct CampaignText {
    std::string_view id;
    std::string_view baseline;
    std::string_view scope;
    std::string_view coverage;
};

struct Evidence {
    Hits hits;
    std::size_t covered{};
    std::size_t total{};
};

struct Choice {
    std::string symbol;
    std::string module;
    Evidence evidence;
    Setting before;
    Setting after;
    std::string_view reason;
    bool loadLate{};
    bool accepted{};
};

struct Bundle {
    std::string identity;
    std::string config;
    std::string changes;
    std::string lateModules;
    std::string report;
};

Result<Campaign> parseCampaign(CampaignText const &inputs);
Result<std::vector<Hits>> parseTrace(std::string_view trace, Campaign const &campaign);
void includeHits(Hits &all, Hits next);
std::vector<Choice> propose(Campaign const &campaign, std::vector<Hits> const &hits);
Bundle materialize(Campaign const &campaign, std::vector<Choice> const &choices);
std::string_view settingText(Setting value);
bool actionable(Choice const &choice);

} // namespace kcfg
