# Evasion bot (Julia).
# Edit ONLY the section marked below. Everything outside it talks to the referee.
# Debug output goes to stderr, never stdout: stdout is reserved for your moves.

const WIDTH = 300
const HEIGHT = 300

mutable struct Player
    x::Int; y::Int; vx::Int; vy::Int
end

# A wall is a Vector{Int} [x1, y1, x2, y2].

# The game state, updated every tick.
mutable struct State
    role::String                # "hunter" or "prey"
    N::Int; M::Int; tick_cap::Int
    tick::Int                   # time steps so far (the prey is asked on odd ticks: 1, 3, 5, ...)
    hunter::Player
    prey::Player
    last_wall::Int              # tick the last wall was built, or -1 if none
    walls::Vector{Vector{Int}}  # [x1, y1, x2, y2] each
    time_used::Float64          # seconds spent thinking so far this game (timed by your bot)
end

# ---------- helpers you may use (a copy of the referee's rules) ----------

# The cells a wall occupies (horizontal or vertical, one cell thick).
function wall_cells(w::Vector{Int})
    x1, y1, x2, y2 = w
    if (x2, y2) < (x1, y1)
        x1, y1, x2, y2 = x2, y2, x1, y1
    end
    cells = Set{Tuple{Int,Int}}()
    if y1 == y2
        for x in x1:x2; push!(cells, (x, y1)); end
    else
        for y in y1:y2; push!(cells, (x1, y)); end
    end
    return cells
end

occupied(walls) = isempty(walls) ? Set{Tuple{Int,Int}}() : union(map(wall_cells, walls)...)

blocked(cells, x, y) = !(0 <= x < WIDTH && 0 <= y < HEIGHT) || (x, y) in cells

# One step of a player at (x, y) moving (vx, vy): returns (x, y, vx, vy). Same as the referee.
function bounce(x, y, vx, vy, cells)
    (vx == 0 && vy == 0) && return (x, y, vx, vy)
    tx, ty = x + vx, y + vy
    blocked(cells, tx, ty) || return (tx, ty, vx, vy)
    (vx == 0 || vy == 0) && return (x, y, -vx, -vy)
    beside, above = blocked(cells, tx, y), blocked(cells, x, ty)
    (beside && above) && return (x, y, -vx, -vy)
    beside && return (x, ty, -vx, vy)
    above && return (tx, y, vx, -vy)
    bx, by = blocked(cells, tx + vx, ty), blocked(cells, tx, ty + vy)
    (bx && by) && return (x, y, -vx, -vy)
    bx && return (tx, y, vx, -vy)
    by && return (x, ty, -vx, vy)
    return (x, y, -vx, -vy)
end

can_build(s::State) = s.last_wall < 0 || s.tick - s.last_wall >= s.N

# The longest wall of this kind ('h' horizontal or 'v' vertical) through the hunter that
# does not touch another wall or the prey. Returns [x1, y1, x2, y2].
function longest_wall(s::State, kind::Char)
    cells = occupied(s.walls)
    dx, dy = kind == 'v' ? (0, 1) : (1, 0)
    free(x, y) = !blocked(cells, x, y) && (x, y) != (s.prey.x, s.prey.y)
    x1, y1 = s.hunter.x, s.hunter.y
    while free(x1 - dx, y1 - dy); x1 -= dx; y1 -= dy; end
    x2, y2 = s.hunter.x, s.hunter.y
    while free(x2 + dx, y2 + dy); x2 += dx; y2 += dy; end
    return [x1, y1, x2, y2]
end

# Do not edit above this line.
# ================== EDIT ONLY THIS SECTION ==================
# Write your strategy in hunter_move and prey_move. You may add helpers here.
# hunter_move returns (remove, create): remove is a Vector of walls, create a wall or nothing.
# prey_move returns (vx, vy) with each in -1, 0, 1, or nothing to keep going as you are.
# A wall that breaks a rule is not built and you are not penalised.
# You have 120 seconds of thinking time per game.

function hunter_move(s::State)
    # Shrinking box: when heading towards the prey, drop a wall behind the hunter across the
    # arena so the prey is trapped on the hunter's side. At M walls, remove the one whose nearest
    # point is farthest from the prey.
    can_build(s) || return (Vector{Int}[], nothing)
    dx, dy = s.prey.x - s.hunter.x, s.prey.y - s.hunter.y
    if abs(dx) >= abs(dy) && abs(dx) > 8 && (dx > 0) == (s.hunter.vx > 0)
        wall = longest_wall(s, 'v')
    elseif abs(dy) > abs(dx) && abs(dy) > 8 && (dy > 0) == (s.hunter.vy > 0)
        wall = longest_wall(s, 'h')
    else
        return (Vector{Int}[], nothing)
    end
    remove = Vector{Int}[]
    if length(s.walls) >= s.M && !isempty(s.walls)
        dist = w -> begin                # distance from the prey to the nearest point of the wall
            x1, x2, y1, y2 = min(w[1], w[3]), max(w[1], w[3]), min(w[2], w[4]), max(w[2], w[4])
            hypot(clamp(s.prey.x, x1, x2) - s.prey.x, clamp(s.prey.y, y1, y2) - s.prey.y)
        end
        push!(remove, s.walls[argmax(dist.(s.walls))])
    end
    return (remove, wall)
end

function prey_move(s::State)
    # Try each of the nine velocities, look a few moves ahead with the hunter bouncing,
    # and pick the one that keeps the prey farthest from the hunter (and off the walls).
    cells = occupied(s.walls)
    best, best_score = nothing, -Inf
    for vx in -1:1, vy in -1:1
        px, py, pvx, pvy = s.prey.x, s.prey.y, vx, vy
        hx, hy, hvx, hvy = s.hunter.x, s.hunter.y, s.hunter.vx, s.hunter.vy
        score = 0.0
        for step in 1:12
            px, py, pvx, pvy = bounce(px, py, pvx, pvy, cells)
            hx, hy, hvx, hvy = bounce(hx, hy, hvx, hvy, cells)
            hx, hy, hvx, hvy = bounce(hx, hy, hvx, hvy, cells)
            d = hypot(px - hx, py - hy)
            score += min(d, 60) / step
            d <= 4 && (score -= 1000 / step)
        end
        edge = min(px, py, WIDTH - 1 - px, HEIGHT - 1 - py)
        score += min(edge, 20) * 0.5
        if score > best_score
            best, best_score = (vx, vy), score
        end
    end
    return best
end

# ============================================================
# Do not edit below this line.

# Minimal readers for the referee's flat JSON messages ("key":value, no spaces).
function keypos(s, key)
    r = findfirst("\"" * key * "\":", s)
    return r === nothing ? nothing : last(r) + 1
end

function num(s, key)
    p = keypos(s, key)
    p === nothing && return 0
    e = p
    while e <= lastindex(s) && (isdigit(s[e]) || s[e] == '-')
        e += 1
    end
    return parse(Int, SubString(s, p, e - 1))
end

function str(s, key)
    p = keypos(s, key)
    (p === nothing || s[p] != '"') && return ""
    q = findnext('"', s, p + 1)
    return String(SubString(s, p + 1, q - 1))
end

function arr(s, key)
    p = keypos(s, key)
    (p === nothing || s[p] != '[') && return Int[]
    q = findnext(']', s, p)
    inner = strip(SubString(s, p + 1, q - 1))
    isempty(inner) && return Int[]
    return [parse(Int, strip(x)) for x in split(inner, ',')]
end

wall_json(w) = "[" * join(w, ",") * "]"

function main()
    st = nothing
    for line in eachline(stdin)
        t = str(line, "type")
        if t == "start"
            st = State(str(line, "role"), num(line, "N"), num(line, "M"), num(line, "tick_cap"), 0,
                       Player(0, 0, 1, 1), Player(230, 200, 0, 0), -1, Vector{Int}[], 0.0)
            try                      # warm up the compiler while the clock is not running
                st.tick = 1; hunter_move(st); prey_move(st); st.tick = 0
            catch
            end
            println("{\"ready\":true}")
            flush(stdout)
        elseif t == "tick"
            st.tick = num(line, "tick")
            st.hunter = Player(num(line, "hx"), num(line, "hy"), num(line, "hvx"), num(line, "hvy"))
            st.prey = Player(num(line, "px"), num(line, "py"), num(line, "pvx"), num(line, "pvy"))
            st.last_wall = num(line, "last_wall")
            flat = arr(line, "walls")
            st.walls = [flat[i:i+3] for i in 1:4:length(flat)-3]
            started = time()
            reply = "{}"
            if st.role == "hunter"
                remove, create = hunter_move(st)
                parts = String[]
                isempty(remove) || push!(parts, "\"remove\":[" * join(wall_json.(remove), ",") * "]")
                create === nothing || push!(parts, "\"create\":" * wall_json(create))
                reply = "{" * join(parts, ",") * "}"
            else
                v = prey_move(st)
                v === nothing || (reply = "{\"velocity\":[" * string(v[1]) * "," * string(v[2]) * "]}")
            end
            st.time_used += time() - started
            println(reply)
            flush(stdout)
        end
    end
end

main()
