#pragma once

#include <kcfg/Plan.h>

#include <memory>

namespace kcfg {

using Values = std::map<std::string, Setting>;

struct Conflict {
    std::string message;
    std::vector<std::string> symbols;
};

struct DependencyGraph {
    virtual ~DependencyGraph() = default;
    virtual std::map<std::string, bool> const &symbols() const = 0; // true for tristates
    virtual std::vector<Conflict> conflicts(Values const &values) const = 0;
};

Result<std::unique_ptr<DependencyGraph>> parseGraph(std::string_view text);

} // namespace kcfg
