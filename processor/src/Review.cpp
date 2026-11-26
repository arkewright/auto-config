#include "Boundary.h"

#include <ftxui/component/component_base.hpp>
#include <ftxui/component/event.hpp>
#include <ftxui/component/screen_interactive.hpp>
#include <ftxui/dom/elements.hpp>

#include <algorithm>
#include <array>
#include <sstream>

namespace kcfg {
namespace {
using namespace ftxui;

enum class Mode { normal, search, command, edit, help, discard };

constexpr std::array helpLines{
    "Browse",
    "  j / k, arrows      Next / previous option",
    "  gg / G             First / last option",
    "  Ctrl-d / Ctrl-u    Half a page down / up",
    "  PgDn / PgUp        Full page (also Ctrl-f / Ctrl-b)",
    "  /text Enter        Filter names, ignoring case; Esc cancels typing",
    "  n / N              Next / previous search match, with wraparound",
    "  Esc                Clear search; d toggles changed options only",
    "  c                  Jump to the next dependency conflict",
    "Edit",
    "  i then y / m / n   Set built-in / module / disabled; Esc cancels",
    "  r                  Release a manual choice back to analysis",
    "  * marks a change; LOCK marks an immutable final opinion.",
    "  a / u              Run automatic analysis / undo the last action",
    "Save and leave",
    "  s or :w            Save the draft and keep editing",
    "  A or :wq           Apply the current config and exit on success",
    "  q or :q            Quit without applying; confirm unsaved edits",
    "  :q!                Discard unsaved edits and exit",
};

bool matches(std::string const &name, std::string const &query) {
    auto const fold = [](char c) { return c >= 'a' and c <= 'z' ? c - ('a' - 'A') : c; };
    return query.empty() or std::search(name.begin(), name.end(), query.begin(), query.end(),
        [&](char a, char b) { return fold(a) == fold(b); }) != name.end();
}

std::string display(Row const &row, Setting value) {
    return value == Setting::other ? row.originalText : std::string{settingText(value)};
}

Element valueCell(std::string value, int width) {
    return text(std::move(value)) | size(WIDTH, EQUAL, width);
}

Element optionRow(Row const &row, bool selected, int width) {
    bool const changed = row.before != row.current;
    auto current = valueCell(display(row, row.current), width);
    if (changed) current |= bold | color(Color::Cyan);
    auto line = hbox({text(selected ? "> " : "  "), text(changed ? "* " : "  "),
        valueCell(display(row, row.before), width), std::move(current),
        text(row.symbol) | xflex, text(row.locked ? " LOCK" : row.late ? " LATE" : "") | bold});
    return selected ? std::move(line) | inverted : line;
}

std::string evidenceText(Row const &row) {
    std::ostringstream out;
    if (row.locked) out << "Locked by final opinion  |  ";
    else if (not row.editable) out << "Read only  |  ";
    auto const &e = row.evidence;
    if (e.total == 0) out << "Outside observation scope";
    else out << "boot=" << e.hits.boot << "  workload=" << e.hits.workload << "  init=" << e.hits.init
             << "  covered=" << e.covered << '/' << e.total;
    return out.str();
}

class Editor final : public ComponentBase {
public:
    Editor(Session &session_, EditorFiles const &files_, ScreenInteractive &screen_)
        : session{session_}, files{files_}, screen{screen_} { refresh(); }

    bool applied() const { return published; }

private:
    Session &session;
    EditorFiles const &files;
    ScreenInteractive &screen;
    Mode mode{Mode::normal};
    std::vector<std::size_t> visible;
    std::vector<Conflict> conflicts;
    std::size_t cursor{}, changedCount{}, conflictIndex{}, helpOffset{};
    std::string filter, previousFilter, command, message;
    std::size_t previousRow{};
    bool changesOnly{}, error{}, awaitingG{}, published{};

    std::size_t rowIndex() const { return visible.empty() ? 0 : visible[cursor]; }

    void filterRows(std::size_t anchor) {
        visible.clear();
        auto const &rows = session.rows();
        for (std::size_t i = 0; i < rows.size(); ++i) {
            auto const &row = rows[i];
            if ((not changesOnly or row.before != row.current) and matches(row.symbol, filter)) visible.push_back(i);
        }
        auto const found = std::ranges::lower_bound(visible, anchor);
        cursor = visible.empty() ? 0 : std::min(visible.size() - 1, static_cast<std::size_t>(found - visible.begin()));
    }

    void refresh() {
        conflicts = session.conflicts();
        conflictIndex = 0;
        changedCount = static_cast<std::size_t>(std::ranges::count_if(session.rows(),
            [](Row const &row) { return row.before != row.current; }));
        filterRows(rowIndex());
    }

    void tell(std::string text, bool failed = false) { message = std::move(text); error = failed; }

    bool outcome(Result<void> result, std::string success) {
        if (not result) { tell(std::move(result).error(), true); return false; }
        tell(std::move(success));
        return true;
    }

    void analyze() { if (outcome(session.analyze(), "Analysis complete")) refresh(); }
    void undo() { if (outcome(session.undo(), "Undone")) refresh(); }
    void save() { outcome(files.save(), "Draft saved"); }
    void apply() {
        if (outcome(files.apply(), "Applied")) { published = true; screen.Exit(); }
    }
    void quit() {
        if (session.dirty()) mode = Mode::discard;
        else screen.Exit();
    }

    void jumpConflict() {
        if (conflicts.empty()) { tell("No dependency conflicts"); return; }
        auto const &issue = conflicts[conflictIndex];
        conflictIndex = (conflictIndex + 1) % conflicts.size();
        auto const &rows = session.rows();
        for (auto const &symbol : issue.symbols) {
            auto const it = std::ranges::lower_bound(rows, symbol, {}, &Row::symbol);
            if (it == rows.end() or it->symbol != symbol) continue;
            filter.clear(); changesOnly = false;
            filterRows(static_cast<std::size_t>(it - rows.begin()));
            break;
        }
        tell(issue.message, true);
    }

    static bool details() { return Terminal::Size().dimy >= 11; }
    std::size_t pageSize() const {
        int const fixed = details() ? 8 + int(not conflicts.empty()) : 5;
        return static_cast<std::size_t>(std::max(1, Terminal::Size().dimy - fixed));
    }

    static std::size_t helpHeight() { return static_cast<std::size_t>(std::max(1, Terminal::Size().dimy - 3)); }
    static std::size_t lastHelpOffset() { return helpLines.size() - std::min(helpLines.size(), helpHeight()); }

    Element help() const {
        Elements body{text("Configuration editor help") | bold, separator()};
        auto const start = std::min(helpOffset, lastHelpOffset());
        for (auto i = start; i < std::min(start + helpHeight(), helpLines.size()); ++i)
            body.push_back(text(helpLines[i]));
        body.push_back(filler());
        body.push_back(text("j/k scroll   ? or Esc return"));
        return vbox(std::move(body));
    }

    Element status() const {
        if (mode == Mode::search) return text("/" + filter + "_") | bold;
        if (mode == Mode::command) return text(":" + command + "_") | bold;
        if (mode == Mode::edit) return text("EDIT   y built-in   m module   n disabled   Esc cancel") | bold;
        if (mode == Mode::discard) return text("Discard unsaved edits?  y quit   n or Esc stay") | bold;
        if (awaitingG) return text("g   Press g again for the first option");
        auto result = text(message.empty() ? "NORMAL" : (error ? "! " : "") + message);
        return error ? std::move(result) | color(Color::RedLight) : result;
    }

    Element OnRender() override {
        if (mode == Mode::help) return help();
        auto const narrow = Terminal::Size().dimx < 72;
        int const width = narrow ? 9 : 12;
        Elements body{hbox({text("Kernel configuration editor") | bold, filler(),
                            text(session.dirty() ? "unsaved draft" : "")})};
        if (details()) {
            std::ostringstream info;
            info << (visible.empty() ? 0 : cursor + 1) << '/' << visible.size() << " shown  |  " << changedCount
                 << " changed  |  " << conflicts.size() << " conflicts";
            if (changesOnly) info << "  |  changed only";
            if (not filter.empty()) info << "  |  /" << filter;
            body.push_back(text(info.str()));
        }
        body.push_back(hbox({text("    "), valueCell("Original", width), valueCell("Current", width), text("Option")}) | bold);
        if (visible.empty()) body.push_back(text("No matching options"));
        else {
            auto const page = pageSize();
            auto const first = cursor / page * page;
            for (auto i = first; i < std::min(first + page, visible.size()); ++i)
                body.push_back(optionRow(session.rows()[visible[i]], i == cursor, width));
        }
        body.push_back(filler());
        body.push_back(separator());
        if (details()) {
            if (not visible.empty()) {
                auto const &row = session.rows()[rowIndex()];
                body.push_back(text(evidenceText(row)));
                body.push_back(text(row.reason));
            } else {
                body.push_back(text(changesOnly ? "d shows unchanged options too; Esc clears the search." :
                                                  "Use / to search again; Esc clears the search."));
                body.push_back(text(""));
            }
            if (not conflicts.empty()) body.push_back(text("Conflict (c to visit): " + conflicts.front().message) | bold);
        }
        body.push_back(status());
        body.push_back(text(narrow ? "? help  / find  i edit  a analyze  u undo  s save  A apply" :
            "? help   j/k move   / search   i edit   a analyze   u undo   s draft   A apply   q quit"));
        return vbox(std::move(body));
    }

    void enterCommand() {
        mode = Mode::normal;
        if (command == "w") save();
        else if (command == "wq" or command == "apply") apply();
        else if (command == "q") quit();
        else if (command == "q!") screen.Exit();
        else if (command == "analyze") analyze();
        else if (command == "undo") undo();
        else if (command == "help") { mode = Mode::help; helpOffset = 0; }
        else tell("Unknown command: " + command, true);
    }

    bool input(Event const &event) {
        bool const searching = mode == Mode::search;
        auto &buffer = searching ? filter : command;
        if (event == Event::Escape) {
            if (searching) { filter = previousFilter; filterRows(previousRow); }
            mode = Mode::normal;
        } else if (event == Event::Return) {
            if (searching) mode = Mode::normal;
            else enterCommand();
        } else {
            if (event == Event::Backspace) { if (not buffer.empty()) buffer.pop_back(); }
            else if (event == Event::CtrlU) buffer.clear();
            else if (event.is_character() and std::ranges::all_of(event.input(), [](char c) { return c >= ' ' and c <= '~'; }))
                buffer += event.input();
            else return false;
            if (searching) filterRows(previousRow);
        }
        return true;
    }

    void editValue(Event const &event) {
        if (event == Event::Escape) { mode = Mode::normal; return; }
        Setting value;
        if (event == Event::y) value = Setting::yes;
        else if (event == Event::m) value = Setting::module;
        else if (event == Event::n) value = Setting::no;
        else return;
        mode = Mode::normal;
        auto const symbol = session.rows()[rowIndex()].symbol;
        if (outcome(session.edit(symbol, value), symbol + " = " + std::string{settingText(value)})) refresh();
    }

    bool OnEvent(Event event) override {
        if (event == Event::CtrlC) { quit(); return true; }
        if (mode == Mode::search or mode == Mode::command) return input(event);
        if (mode == Mode::edit) { editValue(event); return true; }
        if (mode == Mode::discard) {
            if (event == Event::y) screen.Exit();
            else if (event == Event::n or event == Event::Escape) mode = Mode::normal;
            return true;
        }
        if (mode == Mode::help) {
            if (event == Event::Escape or event == Event::Character('?') or event == Event::q) mode = Mode::normal;
            else if (event == Event::k or event == Event::ArrowUp) {
                helpOffset = std::min(helpOffset, lastHelpOffset());
                if (helpOffset > 0) --helpOffset;
            } else if (event == Event::j or event == Event::ArrowDown) helpOffset = std::min(helpOffset + 1, lastHelpOffset());
            return true;
        }
        if (event == Event::g) {
            if (awaitingG) cursor = 0;
            awaitingG = not awaitingG;
            return true;
        }
        awaitingG = false;
        if (event == Event::Character('?')) { mode = Mode::help; helpOffset = 0; }
        else if (event == Event::Character('/')) {
            mode = Mode::search; previousFilter = filter; previousRow = rowIndex(); filter.clear(); filterRows(previousRow); tell("");
        } else if (event == Event::Character(':')) { mode = Mode::command; command.clear(); }
        else if (event == Event::Escape) { auto anchor = rowIndex(); filter.clear(); filterRows(anchor); tell(""); }
        else if (event == Event::d) { changesOnly = not changesOnly; filterRows(rowIndex()); }
        else if (event == Event::a) analyze();
        else if (event == Event::u) undo();
        else if (event == Event::s) save();
        else if (event == Event::A) apply();
        else if (event == Event::q) quit();
        else if (event == Event::c) jumpConflict();
        else if (visible.empty()) return false;
        else if (event == Event::r) {
            if (outcome(session.reset(session.rows()[rowIndex()].symbol), "Manual choice released")) refresh();
        } else if (event == Event::i or event == Event::Return) mode = Mode::edit;
        else if (event == Event::n or event == Event::N) {
            if (filter.empty()) tell("Use / to search first");
            else cursor = (cursor + (event == Event::n ? 1 : visible.size() - 1)) % visible.size();
        } else if (event == Event::G or event == Event::End or event.input() == "\x1b[4~") cursor = visible.size() - 1;
        else if (event == Event::Home or event.input() == "\x1b[1~") cursor = 0;
        else {
            std::size_t step = 1;
            if (event == Event::PageUp or event == Event::PageDown or event == Event::CtrlB or event == Event::CtrlF) step = pageSize();
            else if (event == Event::CtrlU or event == Event::CtrlD) step = std::max<std::size_t>(1, pageSize() / 2);
            if (event == Event::k or event == Event::ArrowUp or event == Event::PageUp or event == Event::CtrlB or event == Event::CtrlU)
                cursor -= std::min(cursor, step);
            else if (event == Event::j or event == Event::ArrowDown or event == Event::PageDown or event == Event::CtrlF or event == Event::CtrlD)
                cursor = std::min(cursor + step, visible.size() - 1);
            else return false;
        }
        return true;
    }
};

} // namespace

bool editConfiguration(Session &session, EditorFiles const &files) {
    auto screen = ftxui::ScreenInteractive::Fullscreen();
    screen.TrackMouse(false);
    screen.ForceHandleCtrlC(false);
    auto editor = std::make_shared<Editor>(session, files, screen);
    screen.Loop(editor);
    return editor->applied();
}

} // namespace kcfg
