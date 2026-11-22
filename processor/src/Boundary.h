#pragma once

#include <kcfg/Plan.h>

#include <filesystem>
#include <span>

namespace kcfg {

Result<Campaign> readCampaign(std::filesystem::path const &directory);
Result<std::vector<Hits>> readRuns(Campaign const &campaign,
                                   std::span<std::filesystem::path const> traces);
Result<void> publishNew(std::filesystem::path const &destination, Bundle const &bundle);
bool reviewChoices(std::vector<Choice> &choices);

} // namespace kcfg
