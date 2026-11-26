#pragma once

#include <kcfg/Graph.h>

#include <memory>
#include <span>

namespace kcfg {

enum struct Authority { baseline, automatic, manual, forced };

struct Row {
    std::string symbol;
    Setting before{};
    Setting current{};
    std::string originalText;
    std::string reason;
    Evidence evidence{};
    bool locked{};
    bool editable{};
    bool late{};
    std::optional<Policy> policy;
    std::string module;
    Authority authority{Authority::baseline};
};

struct Session {
    virtual ~Session() = default;
    virtual std::vector<Row> const &rows() const = 0;
    virtual Result<void> edit(std::string const &symbol, Setting value) = 0;
    virtual Result<void> reset(std::string const &symbol) = 0;
    virtual Result<void> analyze() = 0;
    virtual Result<void> undo() = 0;
    virtual std::vector<Conflict> conflicts() const = 0;

    virtual bool dirty() const = 0;
    virtual std::string draftText() const = 0;
    virtual Result<void> restoreDraft(std::string_view text) = 0;
    virtual void markSaved() = 0;
    virtual Result<Bundle> application() const = 0;
};

Result<std::unique_ptr<Session>> openSession(Campaign const &campaign, std::span<Hits const> hits,
                                             DependencyGraph const &graph, Opinions forced);

} // namespace kcfg
