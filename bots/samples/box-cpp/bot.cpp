// Evasion bot (C++17).
// Edit ONLY the section marked below. Everything outside it talks to the referee.
// Debug output goes to stderr, never stdout: stdout is reserved for your moves.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <iostream>
#include <string>
#include <unordered_set>
#include <vector>

static const int WIDTH = 300, HEIGHT = 300;

struct Player { int x = 0, y = 0, vx = 0, vy = 0; };
struct Wall { int x1, y1, x2, y2; };

// The game state, updated every tick.
struct State {
    std::string role;           // "hunter" or "prey"
    int N = 0, M = 0, tick_cap = 0;
    int tick = 0;               // time steps so far (the prey is asked on odd ticks: 1, 3, 5, ...)
    Player hunter, prey;
    int last_wall = -1;         // tick the last wall was built, or -1 if none
    std::vector<Wall> walls;
    double time_used = 0;       // seconds spent thinking so far this game (timed by your bot)
};

// The hunter's reply: walls to remove, and at most one wall to create.
struct HunterMove { std::vector<Wall> remove; bool create = false; Wall wall{0, 0, 0, 0}; };
// The prey's reply: a new velocity, or keep = true to carry on as you are.
struct PreyMove { bool keep = true; int vx = 0, vy = 0; };

// ---------- helpers you may use (a copy of the referee's rules) ----------

struct Cells {                                   // a set of (x, y) cells
    std::unordered_set<long long> s;
    static long long key(int x, int y) { return (long long)(x + 1) * 1000 + (y + 1); }
    void add(int x, int y) { s.insert(key(x, y)); }
    bool has(int x, int y) const { return s.count(key(x, y)) > 0; }
};

// The cells a wall occupies (horizontal or vertical, one cell thick).
static void wall_cells(Wall w, Cells& out) {
    if (std::make_pair(w.x2, w.y2) < std::make_pair(w.x1, w.y1)) { std::swap(w.x1, w.x2); std::swap(w.y1, w.y2); }
    if (w.y1 == w.y2) { for (int x = w.x1; x <= w.x2; x++) out.add(x, w.y1); }
    else { for (int y = w.y1; y <= w.y2; y++) out.add(w.x1, y); }
}

static Cells occupied(const std::vector<Wall>& walls) { Cells c; for (auto& w : walls) wall_cells(w, c); return c; }

static bool blocked(const Cells& c, int x, int y) {
    return !(0 <= x && x < WIDTH && 0 <= y && y < HEIGHT) || c.has(x, y);
}

// One step of a player moving (vx, vy). Same as the referee.
static Player bounce(Player p, const Cells& c) {
    if (p.vx == 0 && p.vy == 0) return p;
    int tx = p.x + p.vx, ty = p.y + p.vy;
    if (!blocked(c, tx, ty)) { p.x = tx; p.y = ty; return p; }
    if (p.vx == 0 || p.vy == 0) { p.vx = -p.vx; p.vy = -p.vy; return p; }
    bool beside = blocked(c, tx, p.y), above = blocked(c, p.x, ty);
    if (beside && above) { p.vx = -p.vx; p.vy = -p.vy; return p; }
    if (beside) { p.y = ty; p.vx = -p.vx; return p; }
    if (above) { p.x = tx; p.vy = -p.vy; return p; }
    bool bx = blocked(c, tx + p.vx, ty), by = blocked(c, tx, ty + p.vy);
    if (bx && by) { p.vx = -p.vx; p.vy = -p.vy; return p; }
    if (bx) { p.x = tx; p.vy = -p.vy; return p; }
    if (by) { p.y = ty; p.vx = -p.vx; return p; }
    p.vx = -p.vx; p.vy = -p.vy; return p;
}

static bool can_build(const State& s) { return s.last_wall < 0 || s.tick - s.last_wall >= s.N; }

// The longest wall of this kind ('h' horizontal or 'v' vertical) through the hunter
// that does not touch another wall or the prey.
static Wall longest_wall(const State& s, char kind) {
    Cells c = occupied(s.walls);
    int dx = (kind == 'v') ? 0 : 1, dy = (kind == 'v') ? 1 : 0;
    auto free = [&](int x, int y) { return !blocked(c, x, y) && !(x == s.prey.x && y == s.prey.y); };
    Wall w{s.hunter.x, s.hunter.y, s.hunter.x, s.hunter.y};
    while (free(w.x1 - dx, w.y1 - dy)) { w.x1 -= dx; w.y1 -= dy; }
    while (free(w.x2 + dx, w.y2 + dy)) { w.x2 += dx; w.y2 += dy; }
    return w;
}

// Do not edit above this line.
// ================== EDIT ONLY THIS SECTION ==================
// Write your strategy in hunter_move and prey_move. You may add helpers here.
// A wall that breaks a rule is not built and you are not penalised.
// You have 120 seconds of thinking time per game.

static HunterMove hunter_move(const State& s) {
    // Shrinking box: when heading towards the prey, drop a wall behind the hunter across the
    // arena so the prey is trapped on the hunter's side. At M walls, remove the one whose nearest
    // point is farthest from the prey.
    HunterMove m;
    if (!can_build(s)) return m;
    int dx = s.prey.x - s.hunter.x, dy = s.prey.y - s.hunter.y;
    if (std::abs(dx) >= std::abs(dy) && std::abs(dx) > 8 && (dx > 0) == (s.hunter.vx > 0)) m.wall = longest_wall(s, 'v');
    else if (std::abs(dy) > std::abs(dx) && std::abs(dy) > 8 && (dy > 0) == (s.hunter.vy > 0)) m.wall = longest_wall(s, 'h');
    else return m;
    m.create = true;
    if ((int)s.walls.size() >= s.M && !s.walls.empty()) {
        auto dist = [&](const Wall& w) {     // distance from the prey to the nearest point of the wall
            int x1 = std::min(w.x1, w.x2), x2 = std::max(w.x1, w.x2), y1 = std::min(w.y1, w.y2), y2 = std::max(w.y1, w.y2);
            return std::hypot(std::min(std::max(s.prey.x, x1), x2) - s.prey.x, std::min(std::max(s.prey.y, y1), y2) - s.prey.y); };
        m.remove.push_back(*std::max_element(s.walls.begin(), s.walls.end(), [&](const Wall& a, const Wall& b) { return dist(a) < dist(b); }));
    }
    return m;
}

static PreyMove prey_move(const State& s) {
    // Try each of the nine velocities, look a few moves ahead with the hunter bouncing,
    // and pick the one that keeps the prey farthest from the hunter (and off the walls).
    Cells c = occupied(s.walls);
    PreyMove best; double best_score = -1e18;
    for (int vx = -1; vx <= 1; vx++) for (int vy = -1; vy <= 1; vy++) {
        Player p = s.prey, h = s.hunter; p.vx = vx; p.vy = vy;
        double score = 0;
        for (int step = 1; step <= 12; step++) {
            p = bounce(p, c); h = bounce(h, c); h = bounce(h, c);
            double d = std::hypot(p.x - h.x, p.y - h.y);
            score += std::min(d, 60.0) / step;
            if (d <= 4) score -= 1000.0 / step;
        }
        int edge = std::min({p.x, p.y, WIDTH - 1 - p.x, HEIGHT - 1 - p.y});
        score += std::min(edge, 20) * 0.5;
        if (score > best_score) { best_score = score; best.keep = false; best.vx = vx; best.vy = vy; }
    }
    return best;
}

// ============================================================
// Do not edit below this line.

// Minimal readers for the referee's flat JSON messages ("key":value, no spaces).
static size_t keypos(const std::string& s, const std::string& key) {
    size_t p = s.find("\"" + key + "\":");
    return p == std::string::npos ? p : p + key.size() + 3;
}
static long num(const std::string& s, const std::string& key) {
    size_t p = keypos(s, key); if (p == std::string::npos) return 0;
    return std::stol(s.substr(p));
}
static std::string str(const std::string& s, const std::string& key) {
    size_t p = keypos(s, key); if (p == std::string::npos || s[p] != '"') return "";
    size_t e = s.find('"', p + 1); return s.substr(p + 1, e - p - 1);
}
static std::vector<int> arr(const std::string& s, const std::string& key) {
    std::vector<int> out; size_t p = keypos(s, key); if (p == std::string::npos || s[p] != '[') return out;
    size_t e = s.find(']', p); std::string inner = s.substr(p + 1, e - p - 1);
    size_t i = 0;
    while (i < inner.size()) { size_t j = inner.find(',', i); if (j == std::string::npos) j = inner.size();
        out.push_back(std::stoi(inner.substr(i, j - i))); i = j + 1; }
    return out;
}
static std::string wall_json(const Wall& w) {
    return "[" + std::to_string(w.x1) + "," + std::to_string(w.y1) + "," + std::to_string(w.x2) + "," + std::to_string(w.y2) + "]";
}

int main() {
    std::ios::sync_with_stdio(false);
    State s; std::string line;
    while (std::getline(std::cin, line)) {
        std::string type = str(line, "type");
        if (type == "start") {
            s = State(); s.role = str(line, "role"); s.N = num(line, "N"); s.M = num(line, "M"); s.tick_cap = num(line, "tick_cap");
            std::cout << "{\"ready\":true}" << std::endl;
        } else if (type == "tick") {
            s.tick = num(line, "tick");
            s.hunter = {(int)num(line, "hx"), (int)num(line, "hy"), (int)num(line, "hvx"), (int)num(line, "hvy")};
            s.prey = {(int)num(line, "px"), (int)num(line, "py"), (int)num(line, "pvx"), (int)num(line, "pvy")};
            s.last_wall = num(line, "last_wall");
            std::vector<int> flat = arr(line, "walls"); s.walls.clear();
            for (size_t i = 0; i + 3 < flat.size(); i += 4) s.walls.push_back({flat[i], flat[i + 1], flat[i + 2], flat[i + 3]});
            auto t0 = std::chrono::steady_clock::now();
            std::string reply = "{}";
            if (s.role == "hunter") {
                HunterMove m = hunter_move(s);
                std::string r = "{";
                if (!m.remove.empty()) { r += "\"remove\":["; for (size_t i = 0; i < m.remove.size(); i++) r += (i ? "," : "") + wall_json(m.remove[i]); r += "]"; }
                if (m.create) r += std::string(m.remove.empty() ? "" : ",") + "\"create\":" + wall_json(m.wall);
                reply = r + "}";
            } else {
                PreyMove m = prey_move(s);
                if (!m.keep) reply = "{\"velocity\":[" + std::to_string(m.vx) + "," + std::to_string(m.vy) + "]}";
            }
            s.time_used += std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
            std::cout << reply << std::endl;
        }
    }
    return 0;
}
