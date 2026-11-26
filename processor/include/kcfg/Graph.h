#pragma once

#include <kcfg/Plan.h>

#include <memory>

namespace kcfg {

using Values = std::map<std::string, Setting>;

struct Conflict {
    std::string message;
    std::vector<std::string> symbols;
};

struct Switch {
    bool tristate{};
    bool visible{}; // Has a visible Kconfig prompt in the captured baseline.
};

struct DependencyGraph {
    virtual ~DependencyGraph() = default;
    virtual std::map<std::string, Switch> const &symbols() const = 0;
    virtual std::vector<Conflict> conflicts(Values const &values) const = 0;
};

Result<std::unique_ptr<DependencyGraph>> parseGraph(std::string_view text);

} // namespace kcfg
