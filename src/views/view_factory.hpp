#pragma once

#include "views/view.hpp"

#include <memory>
#include <string>

namespace colony
{

namespace game
{
class HeavenEarthCompendium;
}

class ViewFactory
{
  public:
    ViewFactory();

    ViewPtr CreateSimpleTextView(const std::string& id) const;
    ViewPtr CreateHeavenEarthView(const std::string& id) const;

  private:
    [[nodiscard]] std::shared_ptr<game::HeavenEarthCompendium> EnsureCompendium() const;

    mutable std::shared_ptr<game::HeavenEarthCompendium> compendium_;
};

} // namespace colony
