#include "views/view_factory.hpp"

#include "views/simple_text_view.hpp"
#include "views/heaven_earth_view.hpp"

#include "game/heaven_earth_data.hpp"

namespace colony
{

ViewFactory::ViewFactory() = default;

std::shared_ptr<game::HeavenEarthCompendium> ViewFactory::EnsureCompendium() const
{
    if (!compendium_)
    {
        auto instance = std::make_shared<game::HeavenEarthCompendium>();
        instance->LoadDefault();
        compendium_ = std::move(instance);
    }
    return compendium_;
}

ViewPtr ViewFactory::CreateSimpleTextView(const std::string& id) const
{
    return std::make_unique<SimpleTextView>(id);
}

ViewPtr ViewFactory::CreateHeavenEarthView(const std::string& id) const
{
    return std::make_unique<HeavenEarthView>(id, EnsureCompendium());
}

} // namespace colony
