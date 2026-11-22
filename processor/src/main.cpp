#include "Boundary.h"

#include <unistd.h>

#include <algorithm>
#include <iostream>

namespace {

constexpr std::string_view kUsage{"kcfg-plan CAMPAIGN_DIR TRACE OUTPUT_NEW_DIR [--also TRACE]... [--review]"};

struct Arguments {
    std::filesystem::path campaign;
    std::filesystem::path output;
    std::vector<std::filesystem::path> traces;
    bool review{};
};

kcfg::Result<Arguments> arguments(std::span<char const *const> const args) {
    if (args.size() < 4) return std::unexpected{std::string{kUsage}};
    auto parsed = Arguments{.campaign = args[1], .output = args[3], .traces = {args[2]}};
    for (std::size_t i{4}; i < args.size(); ++i) {
        auto const flag = std::string_view{args[i]};
        if (flag == "--review" and not parsed.review) parsed.review = true;
        else if (flag == "--also" and i + 1 < args.size()) parsed.traces.emplace_back(args[++i]);
        else return std::unexpected{std::string{kUsage}};
    }
    return parsed;
}

kcfg::Result<int> run(Arguments const &args) {
    auto const campaign = kcfg::readCampaign(args.campaign);
    if (not campaign) return std::unexpected{campaign.error()};
    auto const runs = kcfg::readRuns(*campaign, args.traces);
    if (not runs) return std::unexpected{runs.error()};
    auto choices = kcfg::propose(*campaign, *runs);
    if (args.review) {
        if (not ::isatty(STDIN_FILENO) or not ::isatty(STDOUT_FILENO))
            return std::unexpected{"--review requires an interactive terminal"};
        if (not kcfg::reviewChoices(choices)) { std::cout << "Cancelled; no output written.\n"; return 2; }
    }
    auto const written = kcfg::publishNew(args.output, kcfg::materialize(*campaign, choices));
    if (not written) return std::unexpected{written.error()};
    auto const accepted = std::ranges::count_if(choices, [](auto const &choice) { return choice.accepted; });
    std::cout << "Wrote candidate: " << accepted << " accepted of " << choices.size() << " scoped symbols.\n";
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
