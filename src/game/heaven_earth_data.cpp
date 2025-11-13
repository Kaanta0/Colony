#include "game/heaven_earth_data.hpp"

#include "json.hpp"

#include <SDL2/SDL.h>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace colony::game
{
namespace
{

std::string NormalizeKey(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::toupper(ch));
    });
    return value;
}

} // namespace

bool HeavenEarthCompendium::LoadFromFile(const std::filesystem::path& filePath)
{
    Reset();

    std::ifstream input{filePath};
    if (!input.is_open())
    {
        return false;
    }

    nlohmann::json document;
    try
    {
        input >> document;
    }
    catch (const std::exception&)
    {
        return false;
    }

    if (!document.is_array())
    {
        return false;
    }

    for (const auto& entry : document)
    {
        if (!entry.is_object())
        {
            continue;
        }

        MartialSoul soul;
        soul.name = entry.value("name", std::string{});
        soul.grade = entry.value("grade", 0);
        soul.category = entry.value("category", std::string{});
        soul.description = entry.value("description", std::string{});

        if (soul.name.empty())
        {
            continue;
        }

        if (entry.contains("affinities") && entry["affinities"].is_array())
        {
            for (const auto& affinity : entry["affinities"])
            {
                if (affinity.is_string())
                {
                    soul.affinities.push_back(affinity.get<std::string>());
                }
            }
        }

        souls_.push_back(std::move(soul));
    }

    if (souls_.empty())
    {
        Reset();
        return false;
    }

    loaded_ = true;
    sourcePath_ = filePath;
    ComputeSummary();
    return true;
}

bool HeavenEarthCompendium::LoadDefault()
{
    const std::filesystem::path defaultPath = ResolveDefaultPath();
    if (defaultPath.empty())
    {
        Reset();
        return false;
    }
    return LoadFromFile(defaultPath);
}

bool HeavenEarthCompendium::IsLoaded() const noexcept
{
    return loaded_;
}

const std::vector<MartialSoul>& HeavenEarthCompendium::Souls() const noexcept
{
    return souls_;
}

const CompendiumSummary& HeavenEarthCompendium::Summary() const noexcept
{
    return summary_;
}

std::filesystem::path HeavenEarthCompendium::SourcePath() const
{
    return sourcePath_;
}

std::vector<const MartialSoul*> HeavenEarthCompendium::TopSouls(std::size_t count) const
{
    std::vector<const MartialSoul*> ranking;
    ranking.reserve(souls_.size());
    for (const auto& soul : souls_)
    {
        ranking.push_back(&soul);
    }

    std::sort(ranking.begin(), ranking.end(), [](const MartialSoul* lhs, const MartialSoul* rhs) {
        if (lhs->grade != rhs->grade)
        {
            return lhs->grade > rhs->grade;
        }
        return lhs->name < rhs->name;
    });

    if (ranking.size() > count)
    {
        ranking.resize(count);
    }
    return ranking;
}

std::vector<std::pair<std::string, int>> HeavenEarthCompendium::TopAffinities(std::size_t count) const
{
    std::vector<std::pair<std::string, int>> result = summary_.affinityCounts;
    if (result.size() > count)
    {
        result.resize(count);
    }
    return result;
}

std::vector<std::pair<int, int>> HeavenEarthCompendium::GradeCountsDescending() const
{
    return summary_.gradeCounts;
}

std::filesystem::path HeavenEarthCompendium::ResolveDefaultPath()
{
    constexpr const char* kRelativePath = "Heaven-and-Earth-main/data/martial_souls.json";

    std::error_code ec;
    std::filesystem::path candidate{kRelativePath};
    if (std::filesystem::exists(candidate, ec))
    {
        return candidate;
    }

    if (char* basePath = SDL_GetBasePath(); basePath != nullptr)
    {
        std::filesystem::path base{basePath};
        SDL_free(basePath);
        std::filesystem::path baseCandidate = base / kRelativePath;
        if (std::filesystem::exists(baseCandidate, ec))
        {
            return baseCandidate;
        }
    }

    return {};
}

void HeavenEarthCompendium::Reset()
{
    loaded_ = false;
    sourcePath_.clear();
    souls_.clear();
    summary_ = {};
}

void HeavenEarthCompendium::ComputeSummary()
{
    summary_ = {};
    if (souls_.empty())
    {
        return;
    }

    std::unordered_map<int, int> gradeCounts;
    std::unordered_map<std::string, int> affinityCounts;
    std::unordered_set<std::string> affinityNames;

    summary_.totalSouls = static_cast<int>(souls_.size());

    for (const auto& soul : souls_)
    {
        summary_.highestGrade = std::max(summary_.highestGrade, soul.grade);
        if (soul.grade == summary_.highestGrade)
        {
            summary_.highestSoulName = soul.name;
        }
        if (soul.grade >= 7)
        {
            ++summary_.rareSouls;
        }

        ++gradeCounts[soul.grade];

        for (const auto& affinity : soul.affinities)
        {
            if (affinity.empty())
            {
                continue;
            }
            std::string key = NormalizeKey(affinity);
            ++affinityCounts[key];
            affinityNames.insert(key);
        }
    }

    summary_.affinityNames.assign(affinityNames.begin(), affinityNames.end());
    std::sort(summary_.affinityNames.begin(), summary_.affinityNames.end());

    summary_.gradeCounts.clear();
    summary_.gradeCounts.reserve(gradeCounts.size());
    for (const auto& [grade, count] : gradeCounts)
    {
        summary_.gradeCounts.emplace_back(grade, count);
    }
    std::sort(summary_.gradeCounts.begin(), summary_.gradeCounts.end(), [](const auto& lhs, const auto& rhs) {
        return lhs.first > rhs.first;
    });

    summary_.affinityCounts.clear();
    summary_.affinityCounts.reserve(affinityCounts.size());
    for (const auto& [affinity, count] : affinityCounts)
    {
        summary_.affinityCounts.emplace_back(affinity, count);
    }
    std::sort(summary_.affinityCounts.begin(), summary_.affinityCounts.end(), [](const auto& lhs, const auto& rhs) {
        if (lhs.second != rhs.second)
        {
            return lhs.second > rhs.second;
        }
        return lhs.first < rhs.first;
    });
}

} // namespace colony::game

