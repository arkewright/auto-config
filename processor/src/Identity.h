#pragma once

#include <kcfg/Plan.h>

namespace kcfg {
Result<std::string> campaignDigest(CampaignText const &inputs);
}
