#pragma once

#include <kcfg/Session.h>

#include <filesystem>
#include <functional>
#include <span>

namespace kcfg {

Result<Campaign> readCampaign(std::filesystem::path const &directory);
Result<std::vector<Hits>> readRuns(Campaign const &campaign,
                                   std::span<std::filesystem::path const> traces);
Result<std::unique_ptr<DependencyGraph>> readGraph(std::filesystem::path const &path);
Result<Opinions> readOpinions(std::filesystem::path const &path);
Result<void> loadDraft(std::filesystem::path const &path, Session &session);
Result<void> saveDraft(std::filesystem::path const &path, Session &session);
Result<void> publishNew(std::filesystem::path const &destination, Bundle const &bundle);

struct EditorFiles {
    std::function<Result<void>()> save;
    std::function<Result<void>()> apply;
};

bool editConfiguration(Session &session, EditorFiles const &files);

} // namespace kcfg
