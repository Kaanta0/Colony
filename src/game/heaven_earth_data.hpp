#pragma once

#include <SDL2/SDL.h>

#include <filesystem>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace colony::game
{

struct MartialSoul
{
    std::string name;
    int grade = 0;
    std::string category;
    std::vector<std::string> affinities;
    std::string description;
};

struct CompendiumSummary
{
    int totalSouls = 0;
    int rareSouls = 0;
    int highestGrade = 0;
    std::string highestSoulName;
    std::vector<std::string> affinityNames;
    std::vector<std::pair<int, int>> gradeCounts; // grade -> count
    std::vector<std::pair<std::string, int>> affinityCounts; // affinity -> count
};

class HeavenEarthCompendium
{
  public:
    bool LoadFromFile(const std::filesystem::path& filePath);
    bool LoadDefault();

    [[nodiscard]] bool IsLoaded() const noexcept;
    [[nodiscard]] const std::vector<MartialSoul>& Souls() const noexcept;
    [[nodiscard]] const CompendiumSummary& Summary() const noexcept;
    [[nodiscard]] std::filesystem::path SourcePath() const;

    [[nodiscard]] std::vector<const MartialSoul*> TopSouls(std::size_t count) const;
    [[nodiscard]] std::vector<std::pair<std::string, int>> TopAffinities(std::size_t count) const;
    [[nodiscard]] std::vector<std::pair<int, int>> GradeCountsDescending() const;

  private:
    [[nodiscard]] static std::filesystem::path ResolveDefaultPath();
    void Reset();
    void ComputeSummary();

    bool loaded_ = false;
    std::filesystem::path sourcePath_{};
    std::vector<MartialSoul> souls_;
    CompendiumSummary summary_{};
};

} // namespace colony::game

