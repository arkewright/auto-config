#include "Boundary.h"

#include <unistd.h>

#include <iostream>

namespace {

using Path = std::filesystem::path;
constexpr std::string_view kUsage{
    "kcfg-plan CAMPAIGN_DIR TRACE OUTPUT_NEW_DIR [--also TRACE]... [--graph FILE] [--force FILE] [--batch]\n"
    "Default: open the editor; --batch runs analysis and applies it without reading a draft.\n"
    "The graph defaults to CAMPAIGN_DIR/dependencies.tsv. Drafts are saved beside OUTPUT_NEW_DIR with suffix .draft."};

struct Arguments {
    Path campaign;
    Path output;
    std::vector<Path> traces;
    std::optional<Path> graph;
    std::optional<Path> force;
    bool batch{};
};

kcfg::Result<Arguments> arguments(std::span<char const *const> const args) {
    if (args.size() < 4) return std::unexpected{std::string{kUsage}};
    auto parsed = Arguments{.campaign = args[1], .output = args[3], .traces = {args[2]}, .graph = {}, .force = {}};
    for (std::size_t i{4}; i < args.size(); ++i) {
        auto const flag = std::string_view{args[i]};
        if (flag == "--batch" and not parsed.batch) parsed.batch = true;
        else if (i + 1 < args.size() and flag == "--also") parsed.traces.emplace_back(args[++i]);
        else if (i + 1 < args.size() and flag == "--force" and not parsed.force) parsed.force = args[++i];
        else if (i + 1 < args.size() and flag == "--graph" and not parsed.graph) parsed.graph = args[++i];
        else return std::unexpected{std::string{kUsage}};
    }
    while (parsed.output.has_relative_path() and parsed.output.filename().empty()) parsed.output = parsed.output.parent_path();
    return parsed;
}

kcfg::Result<int> run(Arguments const &args) {
    if (not args.batch and (not ::isatty(STDIN_FILENO) or not ::isatty(STDOUT_FILENO)))
        return std::unexpected{"editor requires an interactive terminal; use --batch for automatic analysis"};
    auto const campaign = kcfg::readCampaign(args.campaign);
    if (not campaign) return std::unexpected{campaign.error()};
    auto const runs = kcfg::readRuns(*campaign, args.traces);
    if (not runs) return std::unexpected{runs.error()};
    auto const graph = kcfg::readGraph(args.graph.value_or(args.campaign / "dependencies.tsv"));
    if (not graph) return std::unexpected{graph.error()};
    auto opinions = args.force ? kcfg::readOpinions(*args.force) : kcfg::Result<kcfg::Opinions>{kcfg::Opinions{}};
    if (not opinions) return std::unexpected{opinions.error()};
    auto opened = kcfg::openSession(*campaign, *runs, **graph, std::move(*opinions));
    if (not opened) return std::unexpected{opened.error()};
    auto &session = **opened;
    auto apply = [&]() -> kcfg::Result<void> {
        auto const bundle = session.application();
        if (not bundle) return std::unexpected{bundle.error()};
        return kcfg::publishNew(args.output, *bundle);
    };
    if (args.batch) {
        if (auto ok = session.analyze(); not ok) return std::unexpected{ok.error()};
        if (auto ok = apply(); not ok) return std::unexpected{ok.error()};
    } else {
        auto const draft = Path{args.output}.concat(".draft");
        if (auto ok = kcfg::loadDraft(draft, session); not ok) return std::unexpected{ok.error()};
        if (not kcfg::editConfiguration(session, {.save = [&] { return kcfg::saveDraft(draft, session); }, .apply = apply})) {
            std::cout << "No candidate applied.\n";
            return 2;
        }
    }
    for (auto const &row : session.rows()) {
        if (row.current == kcfg::Setting::no and (row.evidence.hits.boot or row.evidence.hits.workload) and
            (row.locked or row.authority == kcfg::Authority::manual))
            std::cerr << row.symbol << ": " << row.reason << '\n';
    }
    std::cout << "Applied configuration to " << args.output.string() << '\n';
    return 0;
}

} // namespace

int main(int const argc, char **const argv) {
    try {
        if (argc == 2 and std::string_view{argv[1]} == "--help") { std::cout << kUsage << '\n'; return 0; }
        auto const args = arguments({argv, static_cast<std::size_t>(argc)});
        if (not args) { std::cerr << args.error() << '\n'; return 1; }
        auto const result = run(*args);
        if (not result) { std::cerr << "kcfg-plan: " << result.error() << '\n'; return 1; }
        return *result;
    } catch (std::exception const &e) {
        std::cerr << "kcfg-plan: " << e.what() << '\n';
        return 1;
    }
}
