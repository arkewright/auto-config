#include <kcfg/Graph.h>

#include <algorithm>
#include <initializer_list>
#include <set>
#include <stdexcept>
#include <utility>
#include <variant>

namespace kcfg {
namespace {

using Text = std::string_view;
using Id = std::size_t;
using Names = std::map<std::string, Id, std::less<>>;

std::vector<Text> split(Text text, char delimiter) {
    std::vector<Text> parts;
    for (auto pos = text.find(delimiter); pos != Text::npos; pos = text.find(delimiter)) {
        parts.push_back(text.substr(0, pos));
        text.remove_prefix(pos + 1);
    }
    parts.push_back(text);
    return parts;
}

bool symbolName(Text name) {
    return name.starts_with("CONFIG_") and name.size() > 7 and
        std::ranges::all_of(name.substr(7), [](char c) {
            return (c >= 'A' and c <= 'Z') or (c >= 'a' and c <= 'z') or (c >= '0' and c <= '9') or c == '_';
        });
}

Id lookup(Names const &names, Text name) {
    auto it = names.find(name);
    if (it == names.end()) throw std::invalid_argument{"undeclared symbol: " + std::string{name}};
    return it->second;
}

enum class Op { value, symbol, negate, conjunction, disjunction, eq, ne, lt, le, gt, ge };
struct Token { Op op; Id operand{}; };
struct Predicate { std::vector<Token> tokens; std::string spelling; };

Predicate compile(Text text, Names const &names) {
    static std::map<Text, Op> const operators{{"!", Op::negate}, {"&&", Op::conjunction}, {"||", Op::disjunction},
        {"=", Op::eq}, {"!=", Op::ne}, {"<", Op::lt}, {"<=", Op::le}, {">", Op::gt}, {">=", Op::ge}};
    Predicate expr{.tokens = {}, .spelling = std::string{text}};
    std::size_t depth{};
    for (auto word : split(text, ' ')) {
        if (auto pos = Text{"nmy"}.find(word); word.size() == 1 and pos != Text::npos) {
            expr.tokens.push_back({Op::value, pos});
            ++depth;
        } else if (auto it = operators.find(word); it != operators.end()) {
            auto arity = it->second == Op::negate ? 1u : 2u;
            if (depth < arity) throw std::invalid_argument{"missing expression operand: " + std::string{word}};
            depth -= arity - 1;
            expr.tokens.push_back({it->second});
        } else {
            expr.tokens.push_back({Op::symbol, lookup(names, word)});
            ++depth;
        }
    }
    if (depth != 1) throw std::invalid_argument{"expected one postfix expression"};
    return expr;
}

std::string describe(Predicate const &expr) {
    auto words = split(expr.spelling, ' ');
    std::vector<std::string> stack;
    for (std::size_t i{}; i < words.size(); ++i) {
        auto op = expr.tokens[i].op;
        if (op == Op::value or op == Op::symbol) stack.emplace_back(words[i]);
        else if (op == Op::negate) stack.back().insert(0, "!");
        else {
            auto right = std::move(stack.back());
            stack.pop_back();
            stack.back() = '(' + stack.back() + ' ' + std::string{words[i]} + ' ' + right + ')';
        }
        if (stack.back().size() > 512) stack.back().replace(508, std::string::npos, " ...");
    }
    return std::move(stack.back());
}

int evaluate(Predicate const &expr, std::vector<int> const &values, std::vector<int> &stack) {
    stack.clear();
    for (auto const token : expr.tokens) {
        if (token.op == Op::value) stack.push_back(static_cast<int>(token.operand));
        else if (token.op == Op::symbol) stack.push_back(values[token.operand]);
        else if (token.op == Op::negate) stack.back() = 2 - stack.back();
        else {
            auto b = stack.back();
            stack.pop_back();
            auto &a = stack.back();
            switch (token.op) {
            case Op::conjunction: a = std::min(a, b); break;
            case Op::disjunction: a = std::max(a, b); break;
            case Op::eq: a = (a == b) * 2; break;
            case Op::ne: a = (a != b) * 2; break;
            case Op::lt: a = (a < b) * 2; break;
            case Op::le: a = (a <= b) * 2; break;
            case Op::gt: a = (a > b) * 2; break;
            case Op::ge: a = (a >= b) * 2; break;
            default: std::unreachable();
            }
        }
    }
    return stack.back();
}

struct Symbol { std::string name; bool tristate; bool available; };
struct Depends { Id source; Predicate condition; };
struct HiddenTarget { Predicate visible; Predicate limit; };
struct LowerBound { Id source; Id target; Predicate condition; std::optional<HiddenTarget> hidden; };
struct Scalar { std::string name; Predicate condition; };
struct ChoiceGroup { bool tristate; bool required; Predicate visible; std::vector<Id> members; };
using Rule = std::variant<Depends, LowerBound, Scalar, ChoiceGroup>;

int promote(int bound, bool tristate) { return bound == 1 and not tristate ? 2 : bound; }

struct GraphImpl final : DependencyGraph {
    Names index;
    std::vector<Symbol> declarations;
    std::map<std::string, Switch> switches;
    std::optional<Id> modules;
    std::vector<Rule> rules;

    std::map<std::string, Switch> const &symbols() const override { return switches; }

    Conflict conflict(std::string message, std::vector<Id> ids, std::initializer_list<Predicate const *> expressions,
                      bool moduleSensitive = false) const {
        for (auto expr : expressions) {
            if (not expr) continue;
            for (auto token : expr->tokens) if (token.op == Op::symbol) ids.push_back(token.operand);
        }
        if (moduleSensitive and modules) ids.push_back(*modules);
        Conflict result{.message = std::move(message), .symbols = {}};
        std::set<Id> seen;
        for (auto id : ids)
            if (declarations[id].available and seen.insert(id).second) result.symbols.push_back(declarations[id].name);
        return result;
    }

    std::vector<Conflict> conflicts(Values const &state) const override {
        std::vector<Conflict> out;
        std::vector<int> values(declarations.size());
        for (auto const &[key, value] : state) {
            if (value != Setting::yes and value != Setting::module) continue;
            auto it = index.find(key);
            if (it == index.end() or not declarations[it->second].available) {
                out.push_back({key + " is enabled but absent from the dependency graph", {key}});
                continue;
            }
            values[it->second] = value == Setting::yes ? 2 : 1;
        }
        bool modular = modules and values[*modules] == 2;
        for (Id i{}; i < values.size(); ++i) {
            if (values[i] != 1) continue;
            auto const &sym = declarations[i];
            if (not sym.tristate) out.push_back({sym.name + " is boolean and cannot be m", {sym.name}});
            else if (not modular) {
                Conflict c{sym.name + "=m needs module support", {sym.name}};
                if (modules) c.symbols.push_back(declarations[*modules].name);
                out.push_back(std::move(c));
            }
        }
        std::vector<int> stack;
        for (auto const &rule : rules) {
            if (auto dep = std::get_if<Depends>(&rule)) {
                if (not values[dep->source]) continue;
                auto const &sym = declarations[dep->source];
                auto upper = evaluate(dep->condition, values, stack);
                if (values[dep->source] > promote(upper, sym.tristate and modular))
                    out.push_back(conflict(sym.name + " depends on " + describe(dep->condition), {dep->source},
                        {&dep->condition}, sym.tristate and upper == 1));
            } else if (auto lower = std::get_if<LowerBound>(&rule)) {
                if (not values[lower->source]) continue;
                auto bound = std::min(values[lower->source], evaluate(lower->condition, values, stack));
                auto const &hidden = lower->hidden;
                if (hidden and bound) {
                    if (evaluate(hidden->visible, values, stack)) continue; // A visible target accepts explicit n.
                    bound = std::min(bound, evaluate(hidden->limit, values, stack));
                }
                auto const &target = declarations[lower->target];
                if (values[lower->target] < promote(bound, target.tristate and modular))
                    out.push_back(conflict(declarations[lower->source].name + (hidden ? " implies hidden " : " selects ") +
                        target.name + " if " + describe(lower->condition), {lower->source, lower->target},
                        {&lower->condition, hidden ? &hidden->visible : nullptr, hidden ? &hidden->limit : nullptr},
                        target.tristate and bound == 1));
            } else if (auto scalar = std::get_if<Scalar>(&rule)) {
                if (not evaluate(scalar->condition, values, stack))
                    out.push_back(conflict(scalar->name + " depends on " + describe(scalar->condition), {}, {&scalar->condition}));
            } else {
                auto const &group = std::get<ChoiceGroup>(rule);
                bool tristate = group.tristate and modular;
                auto vis = promote(evaluate(group.visible, values, stack), tristate);
                std::size_t enabled{}, builtIn{};
                int mode{};
                for (auto member : group.members) {
                    enabled += values[member] != 0;
                    builtIn += values[member] == 2;
                    mode = std::max(mode, values[member]);
                }
                if (mode > vis or (builtIn and enabled != 1) or (mode == 1 and not tristate) or
                    (group.required and not tristate and vis and not enabled))
                    out.push_back(conflict("invalid choice among " + declarations[group.members.front()].name +
                        " and its alternatives", group.members, {&group.visible}, group.tristate));
            }
        }
        return out;
    }
};

std::vector<Id> members(Text list, Names const &names) {
    std::vector<Id> ids;
    for (auto word : split(list, ' ')) ids.push_back(lookup(names, word));
    return ids;
}

} // namespace

Result<std::unique_ptr<DependencyGraph>> parseGraph(Text text) {
    if (not text.starts_with("KCFG-GRAPH\t1\n")) return std::unexpected{"expected KCFG-GRAPH version 1"};
    if (not text.ends_with('\n')) return std::unexpected{"truncated dependency graph: missing final LF"};
    auto graph = std::make_unique<GraphImpl>();
    auto rows = split(text.substr(0, text.size() - 1), '\n');
    std::size_t i{};
    try {
        // Collect declarations first so rule order does not have semantic meaning.
        for (i = 1; i < rows.size(); ++i) {
            auto f = split(rows[i], '\t');
            if (f[0] != "symbol" and f[0] != "absent") continue;
            bool present = f[0] == "symbol";
            bool hidden = present and f.size() == 4 and f[3] == "hidden";
            if ((present ? f.size() != 3 and not hidden : f.size() != 2) or not symbolName(f[1]) or
                (present and f[2] != "bool" and f[2] != "tristate")) throw std::invalid_argument{"invalid symbol declaration"};
            if (not graph->index.emplace(f[1], graph->declarations.size()).second)
                throw std::invalid_argument{"duplicate symbol: " + std::string{f[1]}};
            bool tri = present and f[2] == "tristate";
            graph->declarations.push_back({std::string{f[1]}, tri, present});
            if (present) graph->switches.emplace(f[1], Switch{.tristate = tri, .visible = not hidden});
        }
        if (graph->switches.empty()) return std::unexpected{"no switch declarations in graph"};
        auto const &names = graph->index;
        std::set<Id> choiceMembers;
        for (i = 1; i < rows.size(); ++i) {
            auto f = split(rows[i], '\t');
            auto kind = f[0];
            if (kind == "symbol" or kind == "absent" or (f.size() == 1 and (kind.empty() or kind.starts_with('#')))) continue;
            if (kind == "modules" and f.size() == 2) {
                auto id = lookup(names, f[1]);
                if (graph->modules or graph->declarations[id].tristate or not graph->declarations[id].available)
                    throw std::invalid_argument{"expected one boolean modules symbol"};
                graph->modules = id;
            } else if (kind == "depends" and f.size() == 3) {
                graph->rules.emplace_back(Depends{lookup(names, f[1]), compile(f[2], names)});
            } else if ((kind == "select" and f.size() == 4) or (kind == "imply" and (f.size() == 4 or f.size() == 6))) {
                LowerBound relation{lookup(names, f[1]), lookup(names, f[2]), compile(f[3], names), {}};
                if (f.size() == 6) relation.hidden = HiddenTarget{compile(f[4], names), compile(f[5], names)};
                // An architecture-absent target has no configurable value for select or imply to raise.
                if (not graph->declarations[relation.target].available) continue;
                if (kind == "select" or relation.hidden) graph->rules.emplace_back(std::move(relation));
            } else if (kind == "scalar" and f.size() == 3 and symbolName(f[1]) and not names.contains(f[1])) {
                graph->rules.emplace_back(Scalar{std::string{f[1]}, compile(f[2], names)});
            } else if (kind == "choice" and f.size() == 5 and (f[1] == "bool" or f[1] == "tristate") and
                       (f[2] == "required" or f[2] == "optional")) {
                auto ids = members(f[4], names);
                for (auto id : ids) if (not graph->declarations[id].available or not choiceMembers.insert(id).second)
                    throw std::invalid_argument{"duplicate or unavailable choice member"};
                graph->rules.emplace_back(ChoiceGroup{f[1] == "tristate", f[2] == "required", compile(f[3], names), std::move(ids)});
            } else throw std::invalid_argument{"unknown or malformed graph row"};
        }
    } catch (std::invalid_argument const &e) {
        return std::unexpected{"graph line " + std::to_string(i + 1) + ": " + e.what()};
    }
    return graph;
}

} // namespace kcfg
