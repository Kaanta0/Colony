#pragma once

#include "game/heaven_earth_data.hpp"
#include "views/view.hpp"

#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace colony
{

class HeavenEarthView : public View
{
  public:
    HeavenEarthView(std::string id, std::shared_ptr<game::HeavenEarthCompendium> compendium);

    std::string_view Id() const noexcept override;
    void BindContent(const ViewContent& content) override;
    void Activate(const RenderContext& context) override;
    void Deactivate() override;
    void Render(const RenderContext& context, const SDL_Rect& bounds) override;
    void OnPrimaryAction(std::string& statusBuffer) override;
    [[nodiscard]] std::optional<SDL_Rect> PrimaryActionRect() const override;

  private:
    struct SummaryCard
    {
        std::string label;
        std::string caption;
        SDL_Color accent;
        TextTexture valueTexture;
        TextTexture labelTexture;
        TextTexture captionTexture;
    };

    struct SoulSpotlight
    {
        const game::MartialSoul* soul{};
        SDL_Color accent{160, 120, 255, SDL_ALPHA_OPAQUE};
        TextTexture nameTexture;
        TextTexture affinityTexture;
        TextTexture descriptionTexture;
        TextTexture badgeTexture;
    };

    struct LabelValueRow
    {
        std::string label;
        std::string value;
        TextTexture labelTexture;
        TextTexture valueTexture;
    };

    struct TextCache
    {
        TextTexture heading;
        TextTexture tagline;
        TextTexture datasetSummary;
        TextTexture datasetPath;
        TextTexture primaryActionLabel;
        std::vector<TextTexture> heroHighlights;
        std::vector<SummaryCard> summaryCards;
        std::vector<SoulSpotlight> spotlightCards;
        std::vector<LabelValueRow> affinityRows;
        std::vector<LabelValueRow> gradeRows;
        TextTexture affinityTitle;
        TextTexture gradeTitle;
        TextTexture guideTitle;
        std::vector<TextTexture> paragraphBlocks;
        TextTexture realmTitle;
        std::vector<LabelValueRow> realmRows;
    };

    void ClearTextCache();
    void BuildTextCache(const RenderContext& context);
    void BuildSummaryCards(const RenderContext& context);
    void BuildSpotlights(const RenderContext& context);
    void BuildDistributionRows(const RenderContext& context);
    void BuildParagraphs(const RenderContext& context);
    void BuildRealmRows(const RenderContext& context);

    void RenderHeroSection(const RenderContext& context, const SDL_Rect& bounds, SDL_Rect& outRect) const;
    int RenderSummaryRow(const RenderContext& context, int topY, int originX, int width) const;
    void RenderCompendium(const RenderContext& context, const SDL_Rect& bounds) const;
    int RenderSoulCard(const RenderContext& context, const SDL_Rect& rect, const SoulSpotlight& card) const;
    void RenderAffinityColumn(const RenderContext& context, const SDL_Rect& rect) const;

    [[nodiscard]] SDL_Color ResolveAffinityColor(std::string_view affinity) const;
    [[nodiscard]] std::string FormatTitleCase(std::string_view value) const;
    [[nodiscard]] std::string JoinAffinities(const std::vector<std::string>& affinities) const;

    std::string id_;
    ViewContent content_;
    std::shared_ptr<game::HeavenEarthCompendium> compendium_;
    TextCache textCache_{};
    bool dataAvailable_ = false;

    SDL_Color accentColor_{156, 121, 255, SDL_ALPHA_OPAQUE};
    SDL_Color heroGradientStart_{47, 36, 93, SDL_ALPHA_OPAQUE};
    SDL_Color heroGradientEnd_{20, 14, 48, SDL_ALPHA_OPAQUE};
    SDL_Color heroTextColor_{236, 239, 250, SDL_ALPHA_OPAQUE};

    mutable std::optional<SDL_Rect> primaryActionRect_;
    std::size_t activePrimarySoulIndex_ = 0;
};

} // namespace colony

