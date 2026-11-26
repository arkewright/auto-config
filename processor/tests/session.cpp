#include <kcfg/Session.h>

#include <array>
#include <iostream>
#include <stdexcept>
#include <utility>

namespace {
using namespace kcfg;

void require(bool predicate, char const *message) {
    if (not predicate) throw std::runtime_error{message};
}

template<typename T>
T checked(Result<T> result) {
    if (not result) throw std::runtime_error{result.error()};
    return *std::move(result);
}

void exercise() {
    auto const id = std::string(64, '1') + '\n';
    auto const campaign = checked(parseCampaign({.id = id,
        .baseline = "CONFIG_USED=y\nCONFIG_BASE=y\nCONFIG_SPARE=y\n# CONFIG_LOCK is not set\n",
        .scope = "1\tCONFIG_USED\t-\tdrop\tused.c\n2\tCONFIG_BASE\t-\tdrop\tbase.c\n3\tCONFIG_SPARE\t-\tdrop\tspare.c\n",
        .coverage = "1\t2\n2\t1\n3\t1\n"}));
    auto const graph = checked(parseGraph("KCFG-GRAPH\t1\nsymbol\tCONFIG_USED\tbool\nsymbol\tCONFIG_BASE\tbool\n"
        "symbol\tCONFIG_SPARE\tbool\nsymbol\tCONFIG_LOCK\tbool\ndepends\tCONFIG_USED\tCONFIG_BASE\n"));
    std::array hits{Hits{.workload = true}, Hits{}, Hits{}};
    auto const forced = Opinions{{"CONFIG_LOCK", Setting::yes}};
    auto s = checked(openSession(campaign, hits, *graph, forced));
    auto const initial = checked(s->application());
    require(initial.changes == "CONFIG_LOCK\tn\ty\n", "force applies before analysis");
    require(not s->edit("CONFIG_LOCK", Setting::no) and not s->reset("CONFIG_LOCK"), "force forbids edit and reset");
    require(not s->undo() and not s->dirty(), "a rejected operation is not an edit");

    checked(s->analyze());
    auto const analyzed = checked(s->application());
    require(analyzed.changes == "CONFIG_LOCK\tn\ty\nCONFIG_SPARE\ty\tn\n", "analysis preserves the used dependency");
    checked(s->undo());
    require(checked(s->application()).config == initial.config, "analysis is one undo operation");
    checked(s->edit("CONFIG_BASE", Setting::no));
    require(not s->conflicts().empty() and not s->application(), "a conflicting edit cannot be applied");
    auto const draft = s->draftText();
    s->markSaved();
    checked(s->reset("CONFIG_BASE"));
    require(s->dirty() and s->conflicts().empty(), "releasing a manual edit restores the original");
    checked(s->undo());
    require(not s->dirty() and not s->application(), "undo returns to the saved draft, even if it conflicts");

    auto const newForce = Opinions{{"CONFIG_LOCK", Setting::yes}, {"CONFIG_BASE", Setting::yes}};
    auto fresh = checked(openSession(campaign, hits, *graph, newForce));
    checked(fresh->restoreDraft(draft));
    require(fresh->conflicts().empty() and not fresh->dirty(), "new locks take priority over saved manual edits");
    auto const result = checked(fresh->application());
    require(result.overrides == "CONFIG_BASE\ty\tforced\nCONFIG_LOCK\ty\tforced\n", "even unchanged forced values are recorded");
    require(result.changes == initial.changes and not fresh->undo(), "previous edits cannot undo the new force input");
    require(not fresh->restoreDraft("KCFG-DRAFT\t1\twrong\nanalysis=on\n"), "a draft belongs to its original campaign");
}
} // namespace

int main() {
    try { exercise(); }
    catch (std::exception const &error) { std::cerr << error.what() << '\n'; return 1; }
    return 0;
}
