#pragma once

#include <kcfg/Session.h>

namespace kcfg {

struct Proposal {
    Setting value;
    std::string_view reason;
    bool late{};
};

Proposal automaticChoice(Row const &row, bool modulesEnabled, Switch declaration);
Bundle materialize(Campaign const &campaign, std::span<Row const> rows);

} // namespace kcfg
