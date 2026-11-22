#include "Identity.h"

#include <openssl/evp.h>

#include <array>
#include <memory>

namespace kcfg {

Result<std::string> campaignDigest(CampaignText const &inputs) {
    using namespace std::string_view_literals;
    auto const ctx = std::unique_ptr<EVP_MD_CTX, decltype(&EVP_MD_CTX_free)>{EVP_MD_CTX_new(), EVP_MD_CTX_free};
    if (not ctx or EVP_DigestInit_ex(ctx.get(), EVP_sha256(), nullptr) != 1)
        return std::unexpected{"could not initialize SHA-256"};
    for (auto const part : {"kcfg-scope-v1\0"sv, inputs.seed, inputs.scope, "\0"sv, inputs.baseline}) {
        if (EVP_DigestUpdate(ctx.get(), part.data(), part.size()) != 1)
            return std::unexpected{"could not hash campaign inputs"};
    }
    std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
    unsigned length{};
    if (EVP_DigestFinal_ex(ctx.get(), digest.data(), &length) != 1 or length != 32)
        return std::unexpected{"could not finalize SHA-256"};
    constexpr auto hex = "0123456789abcdef"sv;
    std::string result;
    result.reserve(2 * length);
    for (unsigned i{}; i < length; ++i) {
        result += hex[digest[i] >> 4];
        result += hex[digest[i] & 15];
    }
    return result;
}

} // namespace kcfg
