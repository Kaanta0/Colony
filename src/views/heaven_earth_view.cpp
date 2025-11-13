#include "views/heaven_earth_view.hpp"

#include "game/heaven_earth_data.hpp"
#include "ui/layout.hpp"
#include "utils/color.hpp"
#include "utils/drawing.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <sstream>
#include <utility>

namespace colony
{
namespace
{

constexpr SDL_Color kFallbackAccent{120, 90, 200, SDL_ALPHA_OPAQUE};
constexpr SDL_Color kMutedOverlay{32, 24, 56, 180};
constexpr SDL_Color kSoftHighlight{255, 255, 255, 28};

std::string TrimSpaces(std::string value)
{
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.front())))
    {
        value.erase(value.begin());
    }
    while (!value.empty() && std::isspace(static_cast<unsigned char>(value.back())))
    {
        value.pop_back();
    }
    return value;
}

} // namespace

HeavenEarthView::HeavenEarthView(std::string id, std::shared_ptr<game::HeavenEarthCompendium> compendium)
    : id_(std::move(id)), compendium_(std::move(compendium))
{
    if (compendium_ && !compendium_->IsLoaded())
    {
        compendium_->LoadDefault();
    }
}

std::string_view HeavenEarthView::Id() const noexcept
{
    return id_;
}

void HeavenEarthView::BindContent(const ViewContent& content)
{
    content_ = content;
    primaryActionRect_.reset();
    activePrimarySoulIndex_ = 0;
}

void HeavenEarthView::Activate(const RenderContext& context)
{
    if (compendium_)
    {
        if (!compendium_->IsLoaded())
        {
            compendium_->LoadDefault();
        }
        dataAvailable_ = compendium_->IsLoaded();
    }
    else
    {
        dataAvailable_ = false;
    }

    accentColor_ = color::ParseHexColor(content_.accentColor, kFallbackAccent);
    heroGradientStart_ = color::ParseHexColor(content_.heroGradient[0], accentColor_);
    heroGradientEnd_ = color::ParseHexColor(content_.heroGradient[1], color::Mix(accentColor_, heroGradientStart_, 0.35f));
    heroTextColor_ = SDL_Color{240, 242, 252, SDL_ALPHA_OPAQUE};

    BuildTextCache(context);
}

void HeavenEarthView::Deactivate()
{
    ClearTextCache();
    primaryActionRect_.reset();
}

void HeavenEarthView::Render(const RenderContext& context, const SDL_Rect& bounds)
{
    if (bounds.w <= 0 || bounds.h <= 0)
    {
        return;
    }

    primaryActionRect_.reset();

    SDL_Rect heroRect{};
    RenderHeroSection(context, bounds, heroRect);

    const int summaryTop = heroRect.y + heroRect.h + ui::Scale(24);
    const int summaryBottom = RenderSummaryRow(context, summaryTop, bounds.x, bounds.w);

    const int compendiumTop = summaryBottom + ui::Scale(30);
    const int padding = ui::Scale(28);
    SDL_Rect compendiumBounds{
        bounds.x + padding,
        compendiumTop,
        bounds.w - padding * 2,
        std::max(0, bounds.y + bounds.h - compendiumTop - padding)};
    RenderCompendium(context, compendiumBounds);
}

void HeavenEarthView::OnPrimaryAction(std::string& statusBuffer)
{
    if (!textCache_.spotlightCards.empty())
    {
        if (activePrimarySoulIndex_ >= textCache_.spotlightCards.size())
        {
            activePrimarySoulIndex_ = 0;
        }
        const auto& spotlight = textCache_.spotlightCards[activePrimarySoulIndex_];
        if (spotlight.soul != nullptr)
        {
            statusBuffer = "Codex spotlight: " + spotlight.soul->name + " (Grade "
                + std::to_string(spotlight.soul->grade) + ") ready for briefing.";
        }
        else
        {
            statusBuffer = "Codex overview ready.";
        }
        activePrimarySoulIndex_ = (activePrimarySoulIndex_ + 1) % textCache_.spotlightCards.size();
    }
    else if (dataAvailable_)
    {
        statusBuffer = "Martial soul compendium synchronized.";
    }
    else
    {
        statusBuffer = "No martial soul data available.";
    }
}

std::optional<SDL_Rect> HeavenEarthView::PrimaryActionRect() const
{
    return primaryActionRect_;
}

void HeavenEarthView::ClearTextCache()
{
    auto clearTexture = [](TextTexture& texture) {
        texture.texture.reset();
        texture.width = 0;
        texture.height = 0;
    };

    clearTexture(textCache_.heading);
    clearTexture(textCache_.tagline);
    clearTexture(textCache_.datasetSummary);
    clearTexture(textCache_.datasetPath);
    clearTexture(textCache_.primaryActionLabel);
    clearTexture(textCache_.affinityTitle);
    clearTexture(textCache_.gradeTitle);
    clearTexture(textCache_.guideTitle);
    clearTexture(textCache_.realmTitle);

    for (auto& highlight : textCache_.heroHighlights)
    {
        clearTexture(highlight);
    }
    textCache_.heroHighlights.clear();

    for (auto& card : textCache_.summaryCards)
    {
        clearTexture(card.valueTexture);
        clearTexture(card.labelTexture);
        clearTexture(card.captionTexture);
    }
    textCache_.summaryCards.clear();

    for (auto& spotlight : textCache_.spotlightCards)
    {
        clearTexture(spotlight.nameTexture);
        clearTexture(spotlight.affinityTexture);
        clearTexture(spotlight.descriptionTexture);
        clearTexture(spotlight.badgeTexture);
    }
    textCache_.spotlightCards.clear();

    for (auto& row : textCache_.affinityRows)
    {
        clearTexture(row.labelTexture);
        clearTexture(row.valueTexture);
    }
    textCache_.affinityRows.clear();

    for (auto& row : textCache_.gradeRows)
    {
        clearTexture(row.labelTexture);
        clearTexture(row.valueTexture);
    }
    textCache_.gradeRows.clear();

    for (auto& paragraph : textCache_.paragraphBlocks)
    {
        clearTexture(paragraph);
    }
    textCache_.paragraphBlocks.clear();

    for (auto& row : textCache_.realmRows)
    {
        clearTexture(row.labelTexture);
        clearTexture(row.valueTexture);
    }
    textCache_.realmRows.clear();
}

void HeavenEarthView::BuildTextCache(const RenderContext& context)
{
    ClearTextCache();

    const SDL_Color headingColor = heroTextColor_;
    const SDL_Color mutedColor = SDL_Color{210, 212, 230, SDL_ALPHA_OPAQUE};

    const std::string headingText = content_.heading.empty() ? "Heaven & Earth Codex" : content_.heading;
    textCache_.heading = CreateTextTexture(context.renderer, context.headingFont, headingText, headingColor);

    const std::string taglineText = content_.tagline.empty()
        ? "Bring the cultivation RPG to life with cinematic oversight."
        : content_.tagline;
    textCache_.tagline = CreateTextTexture(context.renderer, context.paragraphFont, taglineText, mutedColor);

    textCache_.heroHighlights.reserve(content_.heroHighlights.size());
    for (const auto& highlight : content_.heroHighlights)
    {
        std::string line = highlight;
        if (line.empty())
        {
            continue;
        }
        textCache_.heroHighlights.push_back(
            CreateTextTexture(context.renderer, context.paragraphFont, line, headingColor));
    }

    BuildSummaryCards(context);
    BuildSpotlights(context);
    BuildDistributionRows(context);
    BuildParagraphs(context);
    BuildRealmRows(context);

    std::ostringstream datasetSummary;
    if (dataAvailable_)
    {
        const auto& summary = compendium_->Summary();
        datasetSummary << summary.totalSouls << " catalogued souls • " << summary.affinityNames.size()
                       << " affinities tracked";
    }
    else
    {
        datasetSummary << "Connect to the bot to load the martial soul library.";
    }
    textCache_.datasetSummary
        = CreateTextTexture(context.renderer, context.buttonFont, datasetSummary.str(), mutedColor);

    std::string sourcePath;
    if (compendium_ && !compendium_->SourcePath().empty())
    {
        sourcePath = "Data source: " + compendium_->SourcePath().generic_string();
    }
    else
    {
        sourcePath = "Data source unavailable";
    }
    textCache_.datasetPath
        = CreateTextTexture(context.renderer, context.paragraphFont, sourcePath, SDL_Color{190, 194, 215, SDL_ALPHA_OPAQUE});

    const std::string primaryLabel = content_.primaryActionLabel.empty() ? "Launch codex" : content_.primaryActionLabel;
    textCache_.primaryActionLabel
        = CreateTextTexture(context.renderer, context.buttonFont, primaryLabel, headingColor);
}

void HeavenEarthView::BuildSummaryCards(const RenderContext& context)
{
    const SDL_Color labelColor{224, 226, 240, SDL_ALPHA_OPAQUE};
    const SDL_Color valueColor{255, 255, 255, SDL_ALPHA_OPAQUE};

    auto makeCard = [&](std::string label, std::string value, std::string caption, SDL_Color accent) {
        SummaryCard card;
        card.label = std::move(label);
        card.caption = std::move(caption);
        card.accent = accent;
        card.valueTexture = CreateTextTexture(context.renderer, context.headingFont, value, valueColor);
        card.labelTexture = CreateTextTexture(context.renderer, context.buttonFont, card.label, labelColor);
        card.captionTexture = CreateTextTexture(context.renderer, context.paragraphFont, card.caption, labelColor);
        textCache_.summaryCards.emplace_back(std::move(card));
    };

    textCache_.summaryCards.clear();
    if (!dataAvailable_)
    {
        makeCard(
            "Awaiting sync",
            "—",
            "Connect Heaven & Earth to populate the codex.",
            color::Mix(accentColor_, SDL_Color{40, 32, 68, SDL_ALPHA_OPAQUE}, 0.5f));
        makeCard(
            "Highlight",
            "Dormant",
            "No martial souls cached in this session yet.",
            color::Mix(accentColor_, SDL_Color{30, 45, 82, SDL_ALPHA_OPAQUE}, 0.4f));
        makeCard(
            "Affinities",
            "0",
            "Elemental spectrum unavailable.",
            color::Mix(accentColor_, SDL_Color{18, 32, 64, SDL_ALPHA_OPAQUE}, 0.6f));
        return;
    }

    const auto& summary = compendium_->Summary();
    std::string totalSouls = std::to_string(summary.totalSouls);
    std::ostringstream rareStream;
    rareStream << "Grade " << summary.highestGrade;
    std::string dominantAffinity = summary.affinityCounts.empty() ? "—" : FormatTitleCase(summary.affinityCounts.front().first);

    makeCard(
        "Martial souls",
        totalSouls,
        "Spirit records synced from the Discord bot.",
        color::Mix(accentColor_, SDL_Color{76, 100, 196, SDL_ALPHA_OPAQUE}, 0.35f));
    makeCard(
        "High-grade focus",
        rareStream.str(),
        summary.highestSoulName.empty() ? "Awaiting discoveries." : summary.highestSoulName,
        color::Mix(accentColor_, SDL_Color{180, 130, 255, SDL_ALPHA_OPAQUE}, 0.42f));
    makeCard(
        "Affinity spectrum",
        dominantAffinity,
        std::to_string(summary.affinityNames.size()) + " elemental lineages observed",
        color::Mix(accentColor_, SDL_Color{88, 150, 255, SDL_ALPHA_OPAQUE}, 0.38f));
}

void HeavenEarthView::BuildSpotlights(const RenderContext& context)
{
    textCache_.spotlightCards.clear();
    if (!dataAvailable_)
    {
        SoulSpotlight card;
        card.soul = nullptr;
        card.accent = color::Mix(accentColor_, SDL_Color{24, 20, 40, SDL_ALPHA_OPAQUE}, 0.6f);
        const std::string fallbackName = "The codex will highlight signature martial souls here.";
        card.nameTexture = CreateTextTexture(context.renderer, context.paragraphFont, fallbackName, heroTextColor_);
        card.affinityTexture
            = CreateTextTexture(context.renderer, context.buttonFont, "Waiting for sync", heroTextColor_);
        textCache_.spotlightCards.emplace_back(std::move(card));
        return;
    }

    auto topSouls = compendium_->TopSouls(3);
    for (const auto* soul : topSouls)
    {
        if (soul == nullptr)
        {
            continue;
        }

        SoulSpotlight card;
        card.soul = soul;
        const SDL_Color baseAccent = ResolveAffinityColor(soul->affinities.empty() ? std::string_view{} : soul->affinities.front());
        card.accent = color::Mix(baseAccent, accentColor_, 0.4f);

        card.nameTexture = CreateTextTexture(context.renderer, context.headingFont, soul->name, heroTextColor_);
        card.descriptionTexture
            = CreateTextTexture(context.renderer, context.paragraphFont, soul->description, heroTextColor_);

        const std::string affinityText = JoinAffinities(soul->affinities);
        std::string categoryText = FormatTitleCase(soul->category);
        if (!categoryText.empty() && !affinityText.empty())
        {
            categoryText += " • ";
        }
        card.affinityTexture = CreateTextTexture(
            context.renderer,
            context.buttonFont,
            categoryText + affinityText,
            SDL_Color{233, 234, 247, SDL_ALPHA_OPAQUE});

        const std::string badgeText = "Grade " + std::to_string(soul->grade);
        card.badgeTexture
            = CreateTextTexture(context.renderer, context.buttonFont, badgeText, SDL_Color{255, 255, 255, SDL_ALPHA_OPAQUE});

        textCache_.spotlightCards.emplace_back(std::move(card));
    }
}

void HeavenEarthView::BuildDistributionRows(const RenderContext& context)
{
    const SDL_Color labelColor{214, 216, 234, SDL_ALPHA_OPAQUE};
    const SDL_Color valueColor{235, 237, 250, SDL_ALPHA_OPAQUE};

    textCache_.affinityRows.clear();
    textCache_.gradeRows.clear();

    if (dataAvailable_)
    {
        auto topAffinities = compendium_->TopAffinities(7);
        for (const auto& [affinity, count] : topAffinities)
        {
            LabelValueRow row;
            row.label = FormatTitleCase(affinity);
            row.value = std::to_string(count) + (count == 1 ? " soul" : " souls");
            row.labelTexture = CreateTextTexture(context.renderer, context.buttonFont, row.label, labelColor);
            row.valueTexture = CreateTextTexture(context.renderer, context.paragraphFont, row.value, valueColor);
            textCache_.affinityRows.emplace_back(std::move(row));
        }

        auto gradeCounts = compendium_->GradeCountsDescending();
        for (const auto& [grade, count] : gradeCounts)
        {
            LabelValueRow row;
            row.label = "Grade " + std::to_string(grade);
            row.value = std::to_string(count) + (count == 1 ? " entry" : " entries");
            row.labelTexture = CreateTextTexture(context.renderer, context.buttonFont, row.label, labelColor);
            row.valueTexture = CreateTextTexture(context.renderer, context.paragraphFont, row.value, valueColor);
            textCache_.gradeRows.emplace_back(std::move(row));
        }
    }

    if (textCache_.affinityRows.empty())
    {
        LabelValueRow row;
        row.label = "Pending sync";
        row.value = "No affinities loaded";
        row.labelTexture = CreateTextTexture(context.renderer, context.buttonFont, row.label, labelColor);
        row.valueTexture = CreateTextTexture(context.renderer, context.paragraphFont, row.value, valueColor);
        textCache_.affinityRows.emplace_back(std::move(row));
    }

    if (textCache_.gradeRows.empty())
    {
        LabelValueRow row;
        row.label = "Unknown";
        row.value = "Awaiting martial soul data";
        row.labelTexture = CreateTextTexture(context.renderer, context.buttonFont, row.label, labelColor);
        row.valueTexture = CreateTextTexture(context.renderer, context.paragraphFont, row.value, valueColor);
        textCache_.gradeRows.emplace_back(std::move(row));
    }

    textCache_.affinityTitle
        = CreateTextTexture(context.renderer, context.headingFont, "Affinity distribution", heroTextColor_);
    textCache_.gradeTitle
        = CreateTextTexture(context.renderer, context.headingFont, "Grade ladder", heroTextColor_);
}

void HeavenEarthView::BuildParagraphs(const RenderContext& context)
{
    textCache_.paragraphBlocks.clear();
    const SDL_Color paragraphColor{212, 214, 231, SDL_ALPHA_OPAQUE};

    for (const auto& paragraph : content_.paragraphs)
    {
        if (paragraph.empty())
        {
            continue;
        }
        textCache_.paragraphBlocks.push_back(
            CreateTextTexture(context.renderer, context.paragraphFont, paragraph, paragraphColor));
    }

    if (textCache_.paragraphBlocks.empty())
    {
        std::string fallback
            = "Orchestrate cultivation events, duels, and expeditions directly from this console.";
        textCache_.paragraphBlocks.push_back(
            CreateTextTexture(context.renderer, context.paragraphFont, fallback, paragraphColor));
    }

    textCache_.guideTitle
        = CreateTextTexture(context.renderer, context.headingFont, "Cultivation loops", heroTextColor_);
}

void HeavenEarthView::BuildRealmRows(const RenderContext& context)
{
    static constexpr std::array<std::pair<std::string_view, std::string_view>, 6> kRealmMilestones = {{
        {"Mortal Realm", "60–80 years of tempered living"},
        {"Qi Condensation", "Sense and guide the world's breath"},
        {"Foundation Establishment", "Forge a stable spiritual core"},
        {"Core Formation", "Ascend toward true cultivation might"},
        {"Nascent Soul", "Manifest a guiding spiritual avatar"},
        {"Ascendant", "Break mortal limits and traverse the heavens"},
    }};

    const SDL_Color labelColor{224, 226, 240, SDL_ALPHA_OPAQUE};
    const SDL_Color valueColor{232, 234, 249, SDL_ALPHA_OPAQUE};

    textCache_.realmRows.clear();
    for (const auto& [realm, description] : kRealmMilestones)
    {
        LabelValueRow row;
        row.label = std::string{realm};
        row.value = std::string{description};
        row.labelTexture = CreateTextTexture(context.renderer, context.buttonFont, row.label, labelColor);
        row.valueTexture = CreateTextTexture(context.renderer, context.paragraphFont, row.value, valueColor);
        textCache_.realmRows.emplace_back(std::move(row));
    }

    textCache_.realmTitle
        = CreateTextTexture(context.renderer, context.headingFont, "Realm milestones", heroTextColor_);
}

void HeavenEarthView::RenderHeroSection(const RenderContext& context, const SDL_Rect& bounds, SDL_Rect& outRect) const
{
    const int padding = ui::Scale(28);
    const int heroHeight = std::max(ui::Scale(280), bounds.h / 3);
    SDL_Rect heroRect{bounds.x + padding, bounds.y + padding, bounds.w - padding * 2, heroHeight};
    outRect = heroRect;

    if (heroRect.w <= 0 || heroRect.h <= 0)
    {
        return;
    }

    const int cornerRadius = ui::Scale(28);
    SDL_SetRenderDrawBlendMode(context.renderer, SDL_BLENDMODE_BLEND);
    SDL_SetRenderDrawColor(context.renderer, accentColor_.r, accentColor_.g, accentColor_.b, 235);
    drawing::RenderFilledRoundedRect(context.renderer, heroRect, cornerRadius);

    SDL_Rect inner{heroRect.x + ui::Scale(4), heroRect.y + ui::Scale(4), heroRect.w - ui::Scale(8), heroRect.h - ui::Scale(8)};
    SDL_Color innerColor = color::Mix(heroGradientStart_, heroGradientEnd_, 0.45f);
    SDL_SetRenderDrawColor(context.renderer, innerColor.r, innerColor.g, innerColor.b, 240);
    drawing::RenderFilledRoundedRect(context.renderer, inner, cornerRadius - ui::Scale(4));

    SDL_Rect overlayTop{inner.x + ui::Scale(6), inner.y + ui::Scale(6), inner.w - ui::Scale(12), inner.h / 2};
    SDL_Color overlayTopColor = color::Mix(heroGradientStart_, SDL_Color{255, 255, 255, SDL_ALPHA_OPAQUE}, 0.12f);
    SDL_SetRenderDrawColor(context.renderer, overlayTopColor.r, overlayTopColor.g, overlayTopColor.b, 200);
    drawing::RenderFilledRoundedRect(
        context.renderer,
        overlayTop,
        cornerRadius - ui::Scale(6),
        drawing::CornerTopLeft | drawing::CornerTopRight);

    SDL_Rect overlayBottom{inner.x + ui::Scale(6), inner.y + inner.h / 2, inner.w - ui::Scale(12), inner.h / 2 - ui::Scale(6)};
    SDL_Color overlayBottomColor = color::Mix(heroGradientEnd_, accentColor_, 0.25f);
    SDL_SetRenderDrawColor(context.renderer, overlayBottomColor.r, overlayBottomColor.g, overlayBottomColor.b, 220);
    drawing::RenderFilledRoundedRect(
        context.renderer,
        overlayBottom,
        cornerRadius - ui::Scale(6),
        drawing::CornerBottomLeft | drawing::CornerBottomRight);

    SDL_Rect accentBar{inner.x, inner.y + ui::Scale(12), ui::Scale(6), inner.h - ui::Scale(24)};
    SDL_SetRenderDrawColor(context.renderer, accentColor_.r, accentColor_.g, accentColor_.b, 255);
    SDL_RenderFillRect(context.renderer, &accentBar);

    const int contentPadding = ui::Scale(28);
    const int leftWidth = inner.w * 5 / 9;
    SDL_Rect left{inner.x + contentPadding, inner.y + contentPadding, leftWidth - contentPadding, inner.h - contentPadding * 2};
    SDL_Rect right{
        inner.x + leftWidth + contentPadding / 2,
        inner.y + contentPadding,
        inner.x + inner.w - contentPadding - (inner.x + leftWidth + contentPadding / 2),
        inner.h - contentPadding * 2};

    int cursorY = left.y;
    if (textCache_.heading.texture)
    {
        SDL_Rect headingRect{left.x, cursorY, textCache_.heading.width, textCache_.heading.height};
        RenderTexture(context.renderer, textCache_.heading, headingRect);
        cursorY += headingRect.h + ui::Scale(14);
    }

    if (textCache_.tagline.texture)
    {
        SDL_Rect taglineRect{left.x, cursorY, textCache_.tagline.width, textCache_.tagline.height};
        RenderTexture(context.renderer, textCache_.tagline, taglineRect);
        cursorY += taglineRect.h + ui::Scale(18);
    }

    int bulletCursorY = cursorY;
    const int bulletSpacing = ui::Scale(20);
    for (const auto& highlight : textCache_.heroHighlights)
    {
        SDL_Rect bulletRect{left.x, bulletCursorY, ui::Scale(10), ui::Scale(10)};
        SDL_SetRenderDrawColor(context.renderer, accentColor_.r, accentColor_.g, accentColor_.b, 255);
        drawing::RenderFilledRoundedRect(context.renderer, bulletRect, ui::Scale(5));

        SDL_Rect textRect{left.x + ui::Scale(18), bulletCursorY - ui::Scale(4), highlight.width, highlight.height};
        RenderTexture(context.renderer, highlight, textRect);

        bulletCursorY += highlight.height + bulletSpacing;
    }

    if (textCache_.datasetSummary.texture)
    {
        SDL_Rect summaryRect{left.x, left.y + left.h - ui::Scale(110), textCache_.datasetSummary.width, textCache_.datasetSummary.height};
        RenderTexture(context.renderer, textCache_.datasetSummary, summaryRect);
    }

    const int buttonHeight = ui::Scale(50);
    const int buttonWidth = std::min(ui::Scale(240), left.w);
    SDL_Rect buttonRect{left.x, left.y + left.h - buttonHeight, buttonWidth, buttonHeight};
    SDL_SetRenderDrawColor(context.renderer, accentColor_.r, accentColor_.g, accentColor_.b, 255);
    drawing::RenderFilledRoundedRect(context.renderer, buttonRect, buttonHeight / 2);
    SDL_Color buttonBorder = color::Mix(accentColor_, SDL_Color{255, 255, 255, SDL_ALPHA_OPAQUE}, 0.2f);
    SDL_SetRenderDrawColor(context.renderer, buttonBorder.r, buttonBorder.g, buttonBorder.b, 255);
    drawing::RenderRoundedRect(context.renderer, buttonRect, buttonHeight / 2);

    if (textCache_.primaryActionLabel.texture)
    {
        SDL_Rect labelRect{
            buttonRect.x + (buttonRect.w - textCache_.primaryActionLabel.width) / 2,
            buttonRect.y + (buttonRect.h - textCache_.primaryActionLabel.height) / 2,
            textCache_.primaryActionLabel.width,
            textCache_.primaryActionLabel.height};
        RenderTexture(context.renderer, textCache_.primaryActionLabel, labelRect);
    }

    primaryActionRect_ = buttonRect;

    int rightCursorY = right.y;
    if (textCache_.datasetPath.texture)
    {
        SDL_Rect pathRect{right.x, rightCursorY, textCache_.datasetPath.width, textCache_.datasetPath.height};
        RenderTexture(context.renderer, textCache_.datasetPath, pathRect);
        rightCursorY += pathRect.h + ui::Scale(16);
    }

    if (!textCache_.spotlightCards.empty())
    {
        const auto& spotlight = textCache_.spotlightCards.front();
        SDL_Color badgeBg = color::Mix(spotlight.accent, SDL_Color{0, 0, 0, SDL_ALPHA_OPAQUE}, 0.25f);

        SDL_Rect badgeRect{right.x, rightCursorY, ui::Scale(120), ui::Scale(36)};
        SDL_SetRenderDrawColor(context.renderer, badgeBg.r, badgeBg.g, badgeBg.b, 230);
        drawing::RenderFilledRoundedRect(context.renderer, badgeRect, badgeRect.h / 2);
        if (spotlight.badgeTexture.texture)
        {
            SDL_Rect badgeText{
                badgeRect.x + (badgeRect.w - spotlight.badgeTexture.width) / 2,
                badgeRect.y + (badgeRect.h - spotlight.badgeTexture.height) / 2,
                spotlight.badgeTexture.width,
                spotlight.badgeTexture.height};
            RenderTexture(context.renderer, spotlight.badgeTexture, badgeText);
        }
        rightCursorY += badgeRect.h + ui::Scale(12);

        if (spotlight.nameTexture.texture)
        {
            SDL_Rect nameRect{right.x, rightCursorY, spotlight.nameTexture.width, spotlight.nameTexture.height};
            RenderTexture(context.renderer, spotlight.nameTexture, nameRect);
            rightCursorY += nameRect.h + ui::Scale(8);
        }

        if (spotlight.affinityTexture.texture)
        {
            SDL_Rect affinityRect{right.x, rightCursorY, spotlight.affinityTexture.width, spotlight.affinityTexture.height};
            RenderTexture(context.renderer, spotlight.affinityTexture, affinityRect);
            rightCursorY += affinityRect.h + ui::Scale(12);
        }

        if (spotlight.descriptionTexture.texture)
        {
            SDL_Rect descriptionRect{
                right.x,
                rightCursorY,
                std::min(spotlight.descriptionTexture.width, right.w),
                spotlight.descriptionTexture.height};
            RenderTexture(context.renderer, spotlight.descriptionTexture, descriptionRect);
            rightCursorY += descriptionRect.h + ui::Scale(12);
        }
    }

    SDL_Rect rightOverlay{right.x, right.y, right.w, right.h};
    SDL_SetRenderDrawColor(context.renderer, kSoftHighlight.r, kSoftHighlight.g, kSoftHighlight.b, kSoftHighlight.a);
    SDL_RenderFillRect(context.renderer, &rightOverlay);
}

int HeavenEarthView::RenderSummaryRow(const RenderContext& context, int topY, int originX, int width) const
{
    if (textCache_.summaryCards.empty())
    {
        return topY;
    }

    const int padding = ui::Scale(28);
    const int cardSpacing = ui::Scale(22);
    const int cardCount = static_cast<int>(textCache_.summaryCards.size());
    const int availableWidth = width - padding * 2 - cardSpacing * (cardCount - 1);
    const int cardWidth = cardCount > 0 ? availableWidth / cardCount : availableWidth;
    const int cardHeight = ui::Scale(150);

    SDL_Rect cardRect{originX + padding, topY, cardWidth, cardHeight};
    for (const auto& card : textCache_.summaryCards)
    {
        SDL_SetRenderDrawColor(context.renderer, card.accent.r, card.accent.g, card.accent.b, 230);
        drawing::RenderFilledRoundedRect(context.renderer, cardRect, ui::Scale(20));

        SDL_Rect inner{cardRect.x + ui::Scale(18), cardRect.y + ui::Scale(18), cardRect.w - ui::Scale(36), cardRect.h - ui::Scale(36)};
        SDL_SetRenderDrawColor(context.renderer, kMutedOverlay.r, kMutedOverlay.g, kMutedOverlay.b, kMutedOverlay.a);
        SDL_RenderFillRect(context.renderer, &inner);

        int cursorY = inner.y;
        if (card.labelTexture.texture)
        {
            SDL_Rect labelRect{inner.x, cursorY, card.labelTexture.width, card.labelTexture.height};
            RenderTexture(context.renderer, card.labelTexture, labelRect);
            cursorY += labelRect.h + ui::Scale(10);
        }

        if (card.valueTexture.texture)
        {
            SDL_Rect valueRect{inner.x, cursorY, card.valueTexture.width, card.valueTexture.height};
            RenderTexture(context.renderer, card.valueTexture, valueRect);
            cursorY += valueRect.h + ui::Scale(12);
        }

        if (card.captionTexture.texture)
        {
            SDL_Rect captionRect{inner.x, cursorY, inner.w, card.captionTexture.height};
            RenderTexture(context.renderer, card.captionTexture, captionRect);
        }

        cardRect.x += cardRect.w + cardSpacing;
    }

    return topY + cardHeight;
}

void HeavenEarthView::RenderCompendium(const RenderContext& context, const SDL_Rect& bounds) const
{
    if (bounds.w <= 0 || bounds.h <= 0)
    {
        return;
    }

    const int columnSpacing = ui::Scale(28);
    const int leftWidth = static_cast<int>(bounds.w * 0.6f);
    SDL_Rect left{bounds.x, bounds.y, leftWidth - columnSpacing / 2, bounds.h};
    SDL_Rect right{
        bounds.x + leftWidth + columnSpacing / 2,
        bounds.y,
        bounds.w - leftWidth - columnSpacing / 2,
        bounds.h};

    int cursorY = left.y;
    const int cardSpacing = ui::Scale(22);
    for (const auto& card : textCache_.spotlightCards)
    {
        SDL_Rect cardRect{left.x, cursorY, left.w, 0};
        const int usedHeight = RenderSoulCard(context, cardRect, card);
        cursorY += usedHeight + cardSpacing;
    }

    if (textCache_.spotlightCards.empty())
    {
        SDL_Rect fallback{left.x, left.y, left.w, ui::Scale(200)};
        SDL_SetRenderDrawColor(context.renderer, accentColor_.r, accentColor_.g, accentColor_.b, 220);
        drawing::RenderFilledRoundedRect(context.renderer, fallback, ui::Scale(22));
        SDL_SetRenderDrawColor(context.renderer, kMutedOverlay.r, kMutedOverlay.g, kMutedOverlay.b, kMutedOverlay.a);
        SDL_Rect inner{fallback.x + ui::Scale(16), fallback.y + ui::Scale(16), fallback.w - ui::Scale(32), fallback.h - ui::Scale(32)};
        SDL_RenderFillRect(context.renderer, &inner);

        if (textCache_.tagline.texture)
        {
            SDL_Rect textRect{inner.x, inner.y, std::min(textCache_.tagline.width, inner.w), textCache_.tagline.height};
            RenderTexture(context.renderer, textCache_.tagline, textRect);
        }
    }

    RenderAffinityColumn(context, right);
}

int HeavenEarthView::RenderSoulCard(const RenderContext& context, const SDL_Rect& rect, const SoulSpotlight& card) const
{
    const int padding = ui::Scale(24);
    int requiredHeight = padding * 2;

    if (card.nameTexture.texture)
    {
        requiredHeight += card.nameTexture.height + ui::Scale(10);
    }
    if (card.affinityTexture.texture)
    {
        requiredHeight += card.affinityTexture.height + ui::Scale(10);
    }
    if (card.descriptionTexture.texture)
    {
        requiredHeight += card.descriptionTexture.height;
    }
    requiredHeight += ui::Scale(40); // space for badge and spacing

    SDL_Rect cardRect{rect.x, rect.y, rect.w, requiredHeight};
    SDL_SetRenderDrawColor(context.renderer, card.accent.r, card.accent.g, card.accent.b, 225);
    drawing::RenderFilledRoundedRect(context.renderer, cardRect, ui::Scale(22));

    SDL_Rect inner{cardRect.x + ui::Scale(18), cardRect.y + ui::Scale(18), cardRect.w - ui::Scale(36), cardRect.h - ui::Scale(36)};
    SDL_SetRenderDrawColor(context.renderer, kMutedOverlay.r, kMutedOverlay.g, kMutedOverlay.b, kMutedOverlay.a);
    SDL_RenderFillRect(context.renderer, &inner);

    SDL_Rect badgeRect{inner.x, inner.y, ui::Scale(110), ui::Scale(34)};
    SDL_Color badgeBg = color::Mix(card.accent, SDL_Color{0, 0, 0, SDL_ALPHA_OPAQUE}, 0.35f);
    SDL_SetRenderDrawColor(context.renderer, badgeBg.r, badgeBg.g, badgeBg.b, 230);
    drawing::RenderFilledRoundedRect(context.renderer, badgeRect, badgeRect.h / 2);
    if (card.badgeTexture.texture)
    {
        SDL_Rect badgeText{
            badgeRect.x + (badgeRect.w - card.badgeTexture.width) / 2,
            badgeRect.y + (badgeRect.h - card.badgeTexture.height) / 2,
            card.badgeTexture.width,
            card.badgeTexture.height};
        RenderTexture(context.renderer, card.badgeTexture, badgeText);
    }

    int cursorY = badgeRect.y + badgeRect.h + ui::Scale(12);
    if (card.nameTexture.texture)
    {
        SDL_Rect nameRect{inner.x, cursorY, std::min(card.nameTexture.width, inner.w), card.nameTexture.height};
        RenderTexture(context.renderer, card.nameTexture, nameRect);
        cursorY += card.nameTexture.height + ui::Scale(10);
    }

    if (card.affinityTexture.texture)
    {
        SDL_Rect affinityRect{inner.x, cursorY, std::min(card.affinityTexture.width, inner.w), card.affinityTexture.height};
        RenderTexture(context.renderer, card.affinityTexture, affinityRect);
        cursorY += card.affinityTexture.height + ui::Scale(12);
    }

    if (card.descriptionTexture.texture)
    {
        SDL_Rect descriptionRect{inner.x, cursorY, std::min(card.descriptionTexture.width, inner.w), card.descriptionTexture.height};
        RenderTexture(context.renderer, card.descriptionTexture, descriptionRect);
    }

    return requiredHeight;
}

void HeavenEarthView::RenderAffinityColumn(const RenderContext& context, const SDL_Rect& rect) const
{
    SDL_Rect background{rect.x, rect.y, rect.w, rect.h};
    SDL_SetRenderDrawColor(context.renderer, accentColor_.r, accentColor_.g, accentColor_.b, 210);
    drawing::RenderFilledRoundedRect(context.renderer, background, ui::Scale(22));
    SDL_Rect inner{rect.x + ui::Scale(16), rect.y + ui::Scale(16), rect.w - ui::Scale(32), rect.h - ui::Scale(32)};
    SDL_SetRenderDrawColor(context.renderer, kMutedOverlay.r, kMutedOverlay.g, kMutedOverlay.b, kMutedOverlay.a);
    SDL_RenderFillRect(context.renderer, &inner);

    int cursorY = inner.y;

    if (textCache_.affinityTitle.texture)
    {
        SDL_Rect titleRect{inner.x, cursorY, textCache_.affinityTitle.width, textCache_.affinityTitle.height};
        RenderTexture(context.renderer, textCache_.affinityTitle, titleRect);
        cursorY += titleRect.h + ui::Scale(12);
    }

    const int rowSpacing = ui::Scale(14);
    for (const auto& row : textCache_.affinityRows)
    {
        SDL_Rect labelRect{inner.x, cursorY, std::min(row.labelTexture.width, inner.w / 2), row.labelTexture.height};
        RenderTexture(context.renderer, row.labelTexture, labelRect);

        SDL_Rect valueRect{inner.x + inner.w - std::min(row.valueTexture.width, inner.w / 2), cursorY, std::min(row.valueTexture.width, inner.w / 2), row.valueTexture.height};
        RenderTexture(context.renderer, row.valueTexture, valueRect);

        cursorY += std::max(labelRect.h, valueRect.h) + rowSpacing;
    }

    cursorY += ui::Scale(10);
    if (textCache_.gradeTitle.texture)
    {
        SDL_Rect titleRect{inner.x, cursorY, textCache_.gradeTitle.width, textCache_.gradeTitle.height};
        RenderTexture(context.renderer, textCache_.gradeTitle, titleRect);
        cursorY += titleRect.h + ui::Scale(12);
    }

    for (const auto& row : textCache_.gradeRows)
    {
        SDL_Rect labelRect{inner.x, cursorY, std::min(row.labelTexture.width, inner.w / 2), row.labelTexture.height};
        RenderTexture(context.renderer, row.labelTexture, labelRect);

        SDL_Rect valueRect{inner.x + inner.w - std::min(row.valueTexture.width, inner.w / 2), cursorY, std::min(row.valueTexture.width, inner.w / 2), row.valueTexture.height};
        RenderTexture(context.renderer, row.valueTexture, valueRect);

        cursorY += std::max(labelRect.h, valueRect.h) + rowSpacing;
    }

    cursorY += ui::Scale(8);
    if (textCache_.guideTitle.texture)
    {
        SDL_Rect titleRect{inner.x, cursorY, textCache_.guideTitle.width, textCache_.guideTitle.height};
        RenderTexture(context.renderer, textCache_.guideTitle, titleRect);
        cursorY += titleRect.h + ui::Scale(12);
    }

    for (const auto& block : textCache_.paragraphBlocks)
    {
        SDL_Rect paragraphRect{inner.x, cursorY, std::min(block.width, inner.w), block.height};
        RenderTexture(context.renderer, block, paragraphRect);
        cursorY += block.height + rowSpacing;
    }

    cursorY += ui::Scale(8);
    if (textCache_.realmTitle.texture)
    {
        SDL_Rect titleRect{inner.x, cursorY, textCache_.realmTitle.width, textCache_.realmTitle.height};
        RenderTexture(context.renderer, textCache_.realmTitle, titleRect);
        cursorY += titleRect.h + ui::Scale(12);
    }

    for (const auto& row : textCache_.realmRows)
    {
        SDL_Rect labelRect{inner.x, cursorY, std::min(row.labelTexture.width, inner.w / 2), row.labelTexture.height};
        RenderTexture(context.renderer, row.labelTexture, labelRect);

        SDL_Rect valueRect{inner.x + ui::Scale(6), cursorY + labelRect.h + ui::Scale(6), std::min(row.valueTexture.width, inner.w - ui::Scale(12)), row.valueTexture.height};
        RenderTexture(context.renderer, row.valueTexture, valueRect);

        cursorY = valueRect.y + valueRect.h + rowSpacing;
    }
}

SDL_Color HeavenEarthView::ResolveAffinityColor(std::string_view affinity) const
{
    std::string key(affinity);
    std::transform(key.begin(), key.end(), key.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });

    if (key == "FIRE")
    {
        return SDL_Color{247, 120, 88, SDL_ALPHA_OPAQUE};
    }
    if (key == "WATER")
    {
        return SDL_Color{86, 149, 255, SDL_ALPHA_OPAQUE};
    }
    if (key == "EARTH")
    {
        return SDL_Color{190, 140, 92, SDL_ALPHA_OPAQUE};
    }
    if (key == "WIND" || key == "AIR")
    {
        return SDL_Color{134, 214, 255, SDL_ALPHA_OPAQUE};
    }
    if (key == "WOOD" || key == "NATURE")
    {
        return SDL_Color{108, 196, 128, SDL_ALPHA_OPAQUE};
    }
    if (key == "METAL")
    {
        return SDL_Color{210, 215, 225, SDL_ALPHA_OPAQUE};
    }
    if (key == "LIGHTNING")
    {
        return SDL_Color{150, 120, 255, SDL_ALPHA_OPAQUE};
    }
    if (key == "LIGHT")
    {
        return SDL_Color{255, 238, 188, SDL_ALPHA_OPAQUE};
    }
    if (key == "DARKNESS")
    {
        return SDL_Color{120, 102, 168, SDL_ALPHA_OPAQUE};
    }
    if (key == "ICE")
    {
        return SDL_Color{148, 210, 255, SDL_ALPHA_OPAQUE};
    }
    if (key == "POISON")
    {
        return SDL_Color{168, 228, 132, SDL_ALPHA_OPAQUE};
    }
    if (key == "VOID")
    {
        return SDL_Color{98, 80, 160, SDL_ALPHA_OPAQUE};
    }

    return accentColor_;
}

std::string HeavenEarthView::FormatTitleCase(std::string_view value) const
{
    std::string result;
    result.reserve(value.size());
    bool newWord = true;
    for (char ch : value)
    {
        if (std::isalnum(static_cast<unsigned char>(ch)))
        {
            if (newWord)
            {
                result.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(ch))));
                newWord = false;
            }
            else
            {
                result.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
            }
        }
        else if (!result.empty() && result.back() != ' ')
        {
            result.push_back(' ');
            newWord = true;
        }
        else
        {
            newWord = true;
        }
    }

    result = TrimSpaces(result);
    return result;
}

std::string HeavenEarthView::JoinAffinities(const std::vector<std::string>& affinities) const
{
    if (affinities.empty())
    {
        return "Unaligned";
    }

    std::ostringstream stream;
    for (std::size_t index = 0; index < affinities.size(); ++index)
    {
        if (index > 0)
        {
            stream << " • ";
        }
        stream << FormatTitleCase(affinities[index]);
    }
    return stream.str();
}

} // namespace colony

