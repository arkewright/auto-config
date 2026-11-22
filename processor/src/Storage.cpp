#include "Boundary.h"

#include <fcntl.h>
#include <linux/fs.h>
#include <sys/stat.h>
#include <unistd.h>

#include <array>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <system_error>
#include <utility>

namespace kcfg {
namespace fs = std::filesystem;
namespace {

constexpr std::size_t kMiB = 1024 * 1024;

std::string failure(fs::path const &path, int const code) {
    return path.string() + ": " + std::error_code{code, std::generic_category()}.message();
}

struct Descriptor {
    explicit Descriptor(int const fd_) : fd{fd_} {}
    ~Descriptor() { ::close(fd); }
    Descriptor(Descriptor const &) = delete;
    Descriptor &operator=(Descriptor const &) = delete;
    int get() const { return fd; }
private:
    int fd;
};

Result<std::string> readFile(fs::path const &path, std::size_t const limit) {
    auto const fd = ::open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return std::unexpected{failure(path, errno)};
    auto const file = Descriptor{fd};
    struct stat info{};
    if (::fstat(file.get(), &info) < 0) return std::unexpected{failure(path, errno)};
    if (not S_ISREG(info.st_mode) or not std::in_range<std::size_t>(info.st_size) or
        static_cast<std::size_t>(info.st_size) > limit)
        return std::unexpected{path.string() + ": expected a size-limited regular file"};
    auto bytes = std::string(static_cast<std::size_t>(info.st_size), '\0');
    std::size_t used{};
    char extra{};
    while (true) {
        auto const full = used == bytes.size();
        auto const count = ::read(file.get(), full ? &extra : bytes.data() + used, full ? 1 : bytes.size() - used);
        if (count < 0 and errno == EINTR) continue;
        if (count < 0) return std::unexpected{failure(path, errno)};
        if (count == 0 and full) return bytes;
        if (count == 0 or full) return std::unexpected{path.string() + ": file changed during read"};
        used += static_cast<std::size_t>(count);
    }
}

struct StagingDirectory {
    explicit StagingDirectory(fs::path path_) : path{std::move(path_)} {}
    StagingDirectory(StagingDirectory const &) = delete;
    StagingDirectory &operator=(StagingDirectory const &) = delete;
    ~StagingDirectory() {
        if (not path.empty()) {
            std::error_code ignored;
            fs::remove_all(path, ignored);
        }
    }

    Result<void> write(std::string_view const name, std::string const &data) const {
        auto const file = path / name;
        auto output = std::ofstream{file, std::ios::binary};
        output << data;
        output.close();
        if (not output) return std::unexpected{file.string() + ": could not write"};
        return {};
    }

    Result<void> publish(fs::path const &destination) {
        if (::renameat2(AT_FDCWD, path.c_str(), AT_FDCWD, destination.c_str(), RENAME_NOREPLACE) != 0)
            return std::unexpected{failure(destination, errno)};
        path.clear();
        return {};
    }

private:
    fs::path path;
};

} // namespace

Result<Campaign> readCampaign(fs::path const &directory) {
    auto const id = readFile(directory / "campaign.id", 65);
    if (not id) return std::unexpected{id.error()};
    auto const seed = readFile(directory / "campaign.seed", 65);
    if (not seed) return std::unexpected{seed.error()};
    auto const baseline = readFile(directory / "baseline.config", 32 * kMiB);
    if (not baseline) return std::unexpected{baseline.error()};
    auto const scope = readFile(directory / "scope.tsv", 64 * kMiB);
    if (not scope) return std::unexpected{scope.error()};
    auto const coverage = readFile(directory / "coverage.tsv", 4 * kMiB);
    if (not coverage) return std::unexpected{coverage.error()};
    return parseCampaign({.id = *id, .seed = *seed, .baseline = *baseline, .scope = *scope, .coverage = *coverage});
}

Result<std::vector<Hits>> readRuns(Campaign const &campaign, std::span<fs::path const> const traces) {
    if (traces.empty()) return std::unexpected{"at least one completed trace is required"};
    auto combined = std::vector<Hits>(campaign.units.size());
    for (auto const &path : traces) {
        auto const bytes = readFile(path, 4 * kMiB);
        if (not bytes) return std::unexpected{bytes.error()};
        auto const run = parseTrace(*bytes, campaign);
        if (not run) return std::unexpected{path.string() + ": " + run.error()};
        for (std::size_t i{}; i < combined.size(); ++i) includeHits(combined[i], (*run)[i]);
    }
    return combined;
}

Result<void> publishNew(fs::path const &destination, Bundle const &bundle) {
    auto target = destination;
    while (target.has_relative_path() and target.filename().empty()) target = target.parent_path();
    auto const leaf = target.filename();
    if (leaf.empty() or leaf == "." or leaf == "..") return std::unexpected{"expected a new output directory name"};
    auto pattern = (target.parent_path() / ".kcfg-plan-XXXXXX").string();
    if (not ::mkdtemp(pattern.data())) return std::unexpected{failure(target.parent_path(), errno)};
    auto stage = StagingDirectory{pattern};
    using File = std::pair<std::string_view, std::string const *>;
    for (auto const &[name, data] : std::array<File, 5>{{{"campaign.id", &bundle.identity}, {"requested.config", &bundle.config},
            {"changes.tsv", &bundle.changes}, {"modules-load.late", &bundle.lateModules}, {"report.tsv", &bundle.report}}}) {
        auto const written = stage.write(name, *data);
        if (not written) return written;
    }
    return stage.publish(target);
}

} // namespace kcfg
