#include "Boundary.h"

#include <ftxui/component/component.hpp>
#include <ftxui/component/event.hpp>
#include <ftxui/component/screen_interactive.hpp>
#include <ftxui/dom/elements.hpp>
#include <ftxui/screen/terminal.hpp>

#include <algorithm>
#include <sstream>

namespace kcfg {
namespace {

std::size_t pageSize() {
    return static_cast<std::size_t>(std::max(1, ftxui::Terminal::Size().dimy - 8));
}

ftxui::Element summary(Choice const &c, bool const focused) {
    std::ostringstream description;
    description << (c.accepted ? "[x] " : "[ ] ") << c.symbol << "  " << settingText(c.before) << " -> "
                << settingText(c.after);
    if (c.loadLate) description << "   load later: " << c.module;
    auto row = ftxui::text(description.str());
    return focused ? std::move(row) | ftxui::inverted : row;
}

} // namespace

bool reviewChoices(std::vector<Choice> &choices) {
    using namespace ftxui;
    std::vector<Choice *> changes;
    for (auto &choice : choices) if (actionable(choice)) changes.push_back(&choice);
    std::size_t cursor{};
    bool saved{};
    auto screen = ScreenInteractive::Fullscreen();
    auto ui = Renderer([&] {
        Elements rows{text("Kernel configuration review") | bold, separator()};
        if (changes.empty()) {
            rows.push_back(text("No eligible changes; protected settings remain unchanged."));
        } else {
            auto const height = pageSize();
            auto const start = cursor / height * height;
            for (auto i = start; i < std::min(changes.size(), start + height); ++i)
                rows.push_back(summary(*changes[i], i == cursor));
            auto const &selected = *changes[cursor];
            auto const &hits = selected.evidence.hits;
            std::ostringstream detail;
            detail << cursor + 1 << '/' << changes.size() << "   boot=" << hits.boot << "  workload=" << hits.workload
                   << "  init=" << hits.init << "  covered=" << selected.evidence.covered << '/' << selected.evidence.total;
            rows.push_back(separator());
            rows.push_back(text(detail.str()));
            rows.push_back(text(std::string{selected.reason}));
        }
        rows.push_back(separator());
        rows.push_back(text("Up/Down: move   Space: toggle   s: save   q/Esc: cancel"));
        return vbox(std::move(rows));
    });
    auto const controls = CatchEvent(ui, [&](Event const event) {
        if (event == Event::Character('s')) { saved = true; screen.Exit(); }
        else if (event == Event::Escape or event == Event::Character('q') or event == Event::CtrlC) screen.Exit();
        else if (changes.empty()) return false;
        else if (event == Event::ArrowUp and cursor > 0) --cursor;
        else if (event == Event::ArrowDown and cursor + 1 < changes.size()) ++cursor;
        else if (event == Event::Home or event.input() == "\x1b[1~") cursor = 0;
        else if (event == Event::End or event.input() == "\x1b[4~") cursor = changes.size() - 1;
        else if (event == Event::PageDown) cursor = std::min(cursor + pageSize(), changes.size() - 1);
        else if (event == Event::PageUp) cursor -= std::min(cursor, pageSize());
        else if (event == Event::Character(' ') or event == Event::Return)
            changes[cursor]->accepted = not changes[cursor]->accepted;
        else return false;
        return true;
    });
    screen.Loop(controls);
    return saved;
}

} // namespace kcfg
