#include "snap.hpp"

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <map>

namespace ldc {

namespace {

constexpr double SCALE_TOL = 1e-4;  // scale / mirror detection
constexpr double DEDUPE_TOL = 1e-3; // LDU

bool isWs(char c) { return std::isspace(static_cast<unsigned char>(c)) != 0; }

std::string lower(std::string_view s) {
    std::string o(s);
    for (char& c : o) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return o;
}
std::string upper(std::string_view s) {
    std::string o(s);
    for (char& c : o) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return o;
}
std::string_view strip(std::string_view s) {
    size_t b = 0, e = s.size();
    while (b < e && isWs(s[b])) ++b;
    while (e > b && isWs(s[e - 1])) --e;
    return s.substr(b, e - b);
}
std::vector<std::string_view> split(std::string_view s) {
    std::vector<std::string_view> out;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && isWs(s[i])) ++i;
        if (i >= s.size()) break;
        const size_t b = i;
        while (i < s.size() && !isWs(s[i])) ++i;
        out.push_back(s.substr(b, i - b));
    }
    return out;
}

// Python float(): optional sign, decimal/exponent, inf/nan
bool pyFloat(std::string_view s, double& out) {
    std::string t;
    t.reserve(s.size());
    for (char c : s) if (c != '_') t += c;
    std::string_view v(t);
    bool neg = false;
    if (!v.empty() && (v[0] == '+' || v[0] == '-')) { neg = v[0] == '-'; v.remove_prefix(1); }
    if (v.empty() || v[0] == '+' || v[0] == '-') return false;
    const auto r = std::from_chars(v.data(), v.data() + v.size(), out);
    if (r.ec != std::errc() || r.ptr != v.data() + v.size()) return false;
    if (neg) out = -out;
    return true;
}

using Params = std::unordered_map<std::string, std::string>;
const std::string* get(const Params& p, const char* key) {
    auto it = p.find(key);
    return it == p.end() ? nullptr : &it->second;
}
std::string getOr(const Params& p, const char* key, const char* def) {
    const std::string* v = get(p, key);
    return v ? *v : std::string(def);
}

// _ARG = \[\s*([^=\]\s]+)\s*=([^\]]*)\]   keys lowercased, values stripped, last wins
Params parseParams(std::string_view text) {
    Params p;
    const size_t n = text.size();
    for (size_t i = 0; i < n; ++i) {
        if (text[i] != '[') continue;
        size_t j = i + 1;
        while (j < n && isWs(text[j])) ++j;
        const size_t ks = j;
        while (j < n && text[j] != '=' && text[j] != ']' && !isWs(text[j])) ++j;
        if (j == ks) continue;
        const std::string_view key = text.substr(ks, j - ks);
        while (j < n && isWs(text[j])) ++j;
        if (j >= n || text[j] != '=') continue;
        ++j;
        const size_t vs = j;
        while (j < n && text[j] != ']') ++j;
        if (j >= n) continue;
        p[lower(key)] = std::string(strip(text.substr(vs, j - vs)));
        i = j;
    }
    return p;
}

// _META = ^\s*0\s+!LDCAD\s+(\S+)(.*)$
bool parseMeta(std::string_view line, std::string_view& cmd, std::string_view& rest) {
    size_t i = 0;
    const size_t n = line.size();
    while (i < n && isWs(line[i])) ++i;
    if (i >= n || line[i] != '0') return false;
    ++i;
    if (i >= n || !isWs(line[i])) return false;
    while (i < n && isWs(line[i])) ++i;
    if (line.substr(i, 6) != "!LDCAD") return false;
    i += 6;
    if (i >= n || !isWs(line[i])) return false;
    while (i < n && isWs(line[i])) ++i;
    const size_t cs = i;
    while (i < n && !isWs(line[i])) ++i;
    if (i == cs) return false;
    cmd = line.substr(cs, i - cs);
    rest = line.substr(i);
    return true;
}

// _floats: exactly n numbers, else default
std::optional<std::vector<double>> floats(const std::string* text, size_t n) {
    std::vector<double> v;
    if (text) {
        for (auto t : split(*text)) {
            double d;
            if (!pyFloat(t, d)) return std::nullopt;
            v.push_back(d);
        }
    }
    if (v.size() != n) return std::nullopt;
    return v;
}
double floatOr(const std::string* text, double def) {
    auto v = floats(text, 1);
    return v ? (*v)[0] : def;
}
bool pyBool(const std::string* text) { return text && lower(strip(*text)) == "true"; }

char genderOf(const std::string* text, char def) {
    const std::string t = text ? upper(strip(*text)) : std::string();
    if (t == "M" || t == "MALE") return 'M';
    if (t == "F" || t == "FEMALE") return 'F';
    return def;
}

void capsOf(const std::string* text, char gender, bool& a, bool& b) {
    std::string t = (text && !text->empty()) ? lower(strip(*text)) : std::string("one");
    if (t == "none") { a = false; b = false; }
    else if (t == "two") { a = true; b = true; }
    else if (t == "a") { a = true; b = false; }
    else if (t == "b") { a = false; b = true; }
    else { a = gender == 'M'; b = gender != 'M'; } // "one"
}

bool secShapeOf(std::string_view s, SecShape& out) {
    const std::string u = upper(s);
    if (u == "R") out = SEC_R;
    else if (u == "A") out = SEC_A;
    else if (u == "S") out = SEC_S;
    else if (u == "_L") out = SEC_UL;
    else if (u == "L_") out = SEC_LU;
    else return false;
    return true;
}

std::vector<Sec> secsOf(const std::string* text) {
    std::vector<Sec> out;
    if (!text) return out;
    const auto toks = split(*text);
    const size_t usable = toks.size() - toks.size() % 3;
    for (size_t i = 0; i < usable; i += 3) {
        Sec s;
        if (!secShapeOf(toks[i], s.shape)) return {};
        if (!pyFloat(toks[i + 1], s.radius) || !pyFloat(toks[i + 2], s.length)) return {};
        out.push_back(s);
    }
    return out;
}

// "[C] Xcnt [C] Zcnt Xstep Zstep" -> local (x, 0, z) offsets
std::vector<Vec3> gridOf(const std::string* text) {
    if (!text || text->empty()) return {{0, 0, 0}};
    const auto toks = split(*text);
    size_t i = 0;
    long counts[2];
    bool centred[2];
    double steps[2];
    bool ok = true;
    for (int a = 0; a < 2 && ok; ++a) {
        if (i >= toks.size()) { ok = false; break; }
        const bool c = upper(toks[i]) == "C";
        i += c ? 1 : 0;
        double d;
        if (i >= toks.size() || !pyFloat(toks[i], d) || !std::isfinite(d)) { ok = false; break; }
        counts[a] = static_cast<long>(std::trunc(d));
        centred[a] = c;
        ++i;
    }
    if (ok && (i + 1 >= toks.size() || !pyFloat(toks[i], steps[0]) || !pyFloat(toks[i + 1], steps[1]))) ok = false;
    if (!ok) {
        std::cerr << "SNAPS: WARNING, Malformed snap grid '" << *text << "'\n";
        return {{0, 0, 0}};
    }

    std::vector<double> axes[2];
    for (int a = 0; a < 2; ++a) {
        const long n = counts[a];
        const double start = centred[a] ? static_cast<double>(-(n - 1)) * steps[a] / 2.0 : 0.0;
        for (long k = 0; k < std::max(n, 1L); ++k) axes[a].push_back(start + static_cast<double>(k) * steps[a]);
    }
    std::vector<Vec3> out;
    for (double x : axes[0])
        for (double z : axes[1]) out.push_back({x, 0.0, z});
    return out;
}

// T(pos) @ R(ori) @ T(gridOffset) [@ S(scale)] for every grid instance
std::vector<Affine> localMatrices(const Params& p, const std::optional<std::vector<double>>& scale) {
    Affine base = Affine::identity();
    const auto pos = floats(get(p, "pos"), 3);
    const auto ori = floats(get(p, "ori"), 9);
    for (int i = 0; i < 3; ++i) {
        base.t[i] = pos ? (*pos)[i] : 0.0;
        for (int j = 0; j < 3; ++j) base.r[i][j] = ori ? (*ori)[3 * i + j] : (i == j ? 1.0 : 0.0);
    }
    std::vector<Affine> out;
    for (const Vec3& off : gridOf(get(p, "grid"))) {
        Affine m = base;
        const double o[3] = {off.x, off.y, off.z};
        for (int i = 0; i < 3; ++i)
            m.t[i] = base.r[i][0] * o[0] + base.r[i][1] * o[1] + base.r[i][2] * o[2] + base.t[i];
        if (scale)
            for (int i = 0; i < 3; ++i)
                for (int j = 0; j < 3; ++j) m.r[i][j] = base.r[i][j] * (*scale)[j];
        out.push_back(m);
    }
    return out;
}

double boundingRadius(const std::vector<std::string>& tokens) {
    std::vector<double> vals;
    for (size_t i = 1; i < tokens.size(); ++i) {
        double d;
        if (!pyFloat(tokens[i], d)) return 0.0;
        vals.push_back(d);
    }
    const std::string kind = tokens.empty() ? std::string("pnt") : lower(tokens[0]);
    if ((kind == "sph" || kind == "cube" || kind == "cyl") && !vals.empty()) return vals[0];
    if (kind == "box" && vals.size() >= 3) return std::max(vals[0], vals[2]);
    return 0.0;
}

bool kindOf(const std::string& cmd, SnapKind& k) {
    if (cmd == "SNAP_CYL") k = SNAP_CYL;
    else if (cmd == "SNAP_CLP") k = SNAP_CLP;
    else if (cmd == "SNAP_FGR") k = SNAP_FGR;
    else if (cmd == "SNAP_GEN") k = SNAP_GEN;
    else if (cmd == "SNAP_SPH") k = SNAP_SPH;
    else return false;
    return true;
}

void addId(std::vector<std::string>& ids, const std::string& id) {
    auto it = std::lower_bound(ids.begin(), ids.end(), id);
    if (it == ids.end() || *it != id) ids.insert(it, id);
}

std::optional<Snap> buildSnap(SnapKind kind, const Params& p, const Affine& local, size_t& badSnaps) {
    Snap s;
    s.kind = kind;
    s.matrix = local;
    s.gender = (kind == SNAP_CLP) ? 'F' : genderOf(get(p, "gender"), 'M');
    s.group = getOr(p, "group", "");
    if (const std::string* id = get(p, "id"); id && !id->empty()) s.ids.push_back(lower(*id));
    s.center = pyBool(get(p, "center"));
    s.slide = pyBool(get(p, "slide"));
    s.scaleRule = lower(getOr(p, "scale", "none"));
    s.mirrorRule = lower(getOr(p, "mirror", kind == SNAP_CYL ? "cor" : "none"));

    switch (kind) {
    case SNAP_CYL: {
        s.secs = secsOf(get(p, "secs"));
        if (s.secs.empty()) { ++badSnaps; return std::nullopt; }
        capsOf(get(p, "caps"), s.gender, s.capA, s.capB);
        s.radius = 0.0;
        s.length = 0.0;
        bool first = true;
        for (const Sec& sec : s.secs) {
            s.radius = first ? sec.radius : std::max(s.radius, sec.radius);
            first = false;
            s.length += sec.length;
        }
        return s;
    }
    case SNAP_CLP:
        s.radius = floatOr(get(p, "radius"), 4.0);
        s.length = floatOr(get(p, "length"), 8.0);
        return s;
    case SNAP_FGR: {
        s.gender = genderOf(get(p, "genderofs"), 'M');
        std::vector<double> seq;
        if (const std::string* t = get(p, "seq")) {
            for (auto tok : split(*t)) {
                double d;
                if (!pyFloat(tok, d)) { seq.clear(); break; }
                seq.push_back(d);
            }
        }
        if (seq.empty()) { ++badSnaps; return std::nullopt; }
        s.radius = floatOr(get(p, "radius"), 0.0);
        const SecShape mine = s.gender == 'M' ? SEC_M : SEC_F;
        const SecShape other = s.gender == 'M' ? SEC_F : SEC_M;
        s.length = 0.0;
        for (size_t i = 0; i < seq.size(); ++i) {
            s.secs.push_back({i % 2 == 0 ? mine : other, s.radius, seq[i]});
            s.length += seq[i];
        }
        return s;
    }
    case SNAP_GEN: {
        const std::string bounding = getOr(p, "bounding", "pnt"); // must outlive the views split() returns
        for (auto tok : split(bounding)) s.bounding.emplace_back(tok);
        s.radius = boundingRadius(s.bounding);
        return s;
    }
    case SNAP_SPH:
        s.radius = floatOr(get(p, "radius"), 0.0);
        return s;
    }
    return std::nullopt;
}

double det3(const double m[3][3]) {
    return m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
         - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
         + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
}

bool readLines(const fs::path& p, std::string& text) {
    std::ifstream f(p, std::ios::binary);
    if (!f) return false;
    text.assign(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
    if (text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xEF &&
        static_cast<unsigned char>(text[1]) == 0xBB && static_cast<unsigned char>(text[2]) == 0xBF)
        text.erase(0, 3);
    return true;
}

} // namespace

const char* snapKindName(SnapKind k) {
    static const char* names[] = {"CYL", "CLP", "FGR", "GEN", "SPH"};
    return names[k];
}
const char* secShapeName(SecShape s) {
    static const char* names[] = {"R", "A", "S", "_L", "L_", "M", "F"};
    return names[s];
}

// ---------------------------------------------------------------------------
// Transforming with LDCad's inheritance rules (_transform)
// ---------------------------------------------------------------------------

std::optional<Snap> transformSnap(const Snap& s, const Affine& M, bool enforce) {
    double C[3][3];
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j)
            C[i][j] = M.r[i][0] * s.matrix.r[0][j] + M.r[i][1] * s.matrix.r[1][j] + M.r[i][2] * s.matrix.r[2][j];

    double sc[3];
    for (int j = 0; j < 3; ++j) {
        sc[j] = std::sqrt(C[0][j] * C[0][j] + C[1][j] * C[1][j] + C[2][j] * C[2][j]);
        if (sc[j] < 1e-9) return std::nullopt;
    }
    const double sx = sc[0], sy = sc[1], sz = sc[2];
    const bool mirrored = det3(C) < 0.0;
    const bool scaled = std::fabs(sx - 1) > SCALE_TOL || std::fabs(sy - 1) > SCALE_TOL || std::fabs(sz - 1) > SCALE_TOL;

    if (enforce) {
        if (mirrored && s.mirrorRule != "cor") return std::nullopt;
        if (scaled) {
            const bool yOnly = std::fabs(sx - 1) <= SCALE_TOL && std::fabs(sz - 1) <= SCALE_TOL;
            const bool rOnly = std::fabs(sx - sz) <= SCALE_TOL && std::fabs(sy - 1) <= SCALE_TOL;
            const std::string& rule = s.scaleRule;
            if (!((rule == "yonly" && yOnly) || (rule == "ronly" && rOnly) || (rule == "yandr" && (yOnly || rOnly))))
                return std::nullopt;
        }
    }

    Snap o = s;
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) o.matrix.r[i][j] = C[i][j] / sc[j];
    if (det3(o.matrix.r) < 0.0)
        for (int i = 0; i < 3; ++i) o.matrix.r[i][0] = -o.matrix.r[i][0]; // "cor": flip one radius axis
    for (int i = 0; i < 3; ++i)
        o.matrix.t[i] = M.r[i][0] * s.matrix.t[0] + M.r[i][1] * s.matrix.t[1] + M.r[i][2] * s.matrix.t[2] + M.t[i];

    if (scaled) {
        const double rS = (sx + sz) / 2.0, yS = sy;
        for (Sec& sec : o.secs) { sec.radius *= rS; sec.length *= yS; }
        o.radius = s.radius * rS;
        o.length = s.length * yS;
    }
    return o;
}

// ---------------------------------------------------------------------------
// Shadow library
// ---------------------------------------------------------------------------

ShadowLibrary::ShadowLibrary(const fs::path& root, const Library& lib) : lib_(lib) {
    std::error_code ec;
    root_ = fs::absolute(root, ec).lexically_normal();
    std::vector<fs::path> seen;
    for (const char* sub : {"offLib", "unOffLib", "."}) {
        const fs::path base = (root_ / sub).lexically_normal();
        for (const char* folder : {"parts", "p"}) {
            const fs::path cand = (base / folder).lexically_normal();
            if (!fs::is_directory(cand, ec)) continue;
            if (std::find(seen.begin(), seen.end(), cand) != seen.end()) continue;
            seen.push_back(cand);

            Dir d;
            d.folder = folder;
            const std::string baseStr = cand.generic_string();
            fs::recursive_directory_iterator it(cand, fs::directory_options::skip_permission_denied, ec), end;
            for (; !ec && it != end; it.increment(ec)) {
                if (!it->is_regular_file(ec)) continue;
                const std::string gen = it->path().generic_string();
                if (gen.size() <= baseStr.size() + 1) continue;
                d.files.emplace(normalisePath(std::string_view(gen).substr(baseStr.size() + 1)), it->path());
            }
            dirs_.push_back(std::move(d));
        }
    }
}

size_t ShadowLibrary::size() const {
    size_t n = 0;
    for (const Dir& d : dirs_) n += d.files.size();
    return n;
}

// resolveShadowPart: library files mirror their own folder (parts/ -> shadow parts/,
// p/ -> shadow p/); local (overlay-like) files try parts/ then p/. First base wins.
std::optional<fs::path> ShadowLibrary::resolveShadowPart(const fs::path& file) const {
    const fs::path f = file.lexically_normal();
    const fs::path& root = lib_.root();
    const fs::path libDirs[3] = {root / "p", root / "parts", root / "models"};

    std::vector<std::pair<fs::path, bool>> bases; // (base, isOverlay)
    const fs::path local = lib_.localDir().lexically_normal();
    if (!local.empty() && std::find(std::begin(libDirs), std::end(libDirs), local) == std::end(libDirs))
        bases.emplace_back(local, true);
    for (const fs::path& d : libDirs) bases.emplace_back(d, false);

    for (const auto& [base, isOverlay] : bases) {
        const fs::path rel = f.lexically_relative(base);
        if (rel.empty() || *rel.begin() == "..") continue;
        const std::string key = normalisePath(rel.generic_string());
        const std::vector<std::string> folders = isOverlay ? std::vector<std::string>{"parts", "p"}
                                                           : std::vector<std::string>{base.filename().string()};
        for (const std::string& folder : folders)
            for (const Dir& d : dirs_)
                if (d.folder == folder)
                    if (auto it = d.files.find(key); it != d.files.end()) return it->second;
        return std::nullopt;
    }
    return std::nullopt;
}

// resolveShadowRef: SNAP_INCL refs resolve directly in the shadow library, p/ before parts/
std::optional<fs::path> ShadowLibrary::resolveShadowRef(std::string_view ref) const {
    const std::string key = normalisePath(ref);
    for (const char* folder : {"p", "parts"})
        for (const Dir& d : dirs_)
            if (d.folder == folder)
                if (auto it = d.files.find(key); it != d.files.end()) return it->second;
    return std::nullopt;
}

void ShadowLibrary::include(const Params& p, const fs::path& owner, ShadowRecords& out) {
    const std::string ref = getOr(p, "ref", "");
    const auto refPath = ref.empty() ? std::nullopt : resolveShadowRef(ref);
    if (!refPath) {
        ++stats_.missingIncludes;
        std::cerr << "SNAPS: WARNING, SNAP_INCL ref '" << ref << "' not found in shadow library ("
                  << owner.filename().string() << ")\n";
        return;
    }
    const auto scale = floats(get(p, "scale"), 3);
    const std::string* id = get(p, "id");
    const std::string inclId = (id && !id->empty()) ? lower(*id) : std::string();

    const ShadowRecords& incl = readShadow(*refPath, false);
    for (const Affine& local : localMatrices(p, scale)) {
        for (const ShadowRecord& r : incl) {
            if (r.isClear) continue;
            auto t = transformSnap(*r.snap, local, false); // include scale is deliberate
            if (!t) continue;
            if (!inclId.empty()) addId(t->ids, inclId);
            out.push_back({false, {}, std::make_shared<const Snap>(std::move(*t))});
        }
    }
}

const ShadowRecords& ShadowLibrary::readShadow(const fs::path& path, bool followIncludes) {
    const std::string key = path.string() + (followIncludes ? "|1" : "|0");
    if (auto it = cache_.find(key); it != cache_.end()) return it->second;

    ShadowRecords records;
    std::string text;
    if (readLines(path, text)) {
        ++stats_.filesRead;
        size_t pos = 0;
        while (pos < text.size()) {
            size_t nl = text.find('\n', pos);
            if (nl == std::string::npos) nl = text.size();
            std::string_view line(text.data() + pos, nl - pos);
            pos = nl + 1;
            if (!line.empty() && line.back() == '\r') line.remove_suffix(1);

            std::string_view cmdView, rest;
            if (!parseMeta(line, cmdView, rest)) continue;
            const std::string cmd = upper(cmdView);
            const Params p = parseParams(rest);

            SnapKind kind;
            if (cmd == "SNAP_CLEAR") {
                records.push_back({true, lower(getOr(p, "id", "")), nullptr});
            } else if (cmd == "SNAP_INCL") {
                if (followIncludes) include(p, path, records);
                // nested SNAP_INCL inside an included file is not followed (as snapping.py)
            } else if (kindOf(cmd, kind)) {
                for (const Affine& local : localMatrices(p, std::nullopt)) {
                    auto s = buildSnap(kind, p, local, stats_.badSnaps);
                    if (s) records.push_back({false, {}, std::make_shared<const Snap>(std::move(*s))});
                }
            }
        }
    }
    return cache_.emplace(key, std::move(records)).first->second;
}

const ShadowRecords* ShadowLibrary::recordsFor(const fs::path& partFile) {
    const std::string key = partFile.lexically_normal().string();
    auto it = partCache_.find(key);
    if (it == partCache_.end()) it = partCache_.emplace(key, resolveShadowPart(partFile)).first;
    if (!it->second) return nullptr;
    return &readShadow(*it->second, true);
}

// ---------------------------------------------------------------------------
// Top-level resolution + dedupe
// ---------------------------------------------------------------------------

namespace {

// Python round(x, n): correctly rounded, ties to even on the exact value (printf does the same)
std::string pyRoundKey(double x, int nd) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.*f", nd, x);
    double r = std::strtod(buf, nullptr);
    if (r == 0.0) r = 0.0; // -0.0 == 0.0 as a dict key
    std::snprintf(buf, sizeof buf, "%.17g", r);
    return buf;
}
// np.round(x, n): rint(x * 10^n) / 10^n
std::string npRoundKey(double x, int nd) {
    const double f = std::pow(10.0, nd);
    double r = std::nearbyint(x * f) / f;
    if (r == 0.0) r = 0.0;
    char buf[64];
    std::snprintf(buf, sizeof buf, "%.17g", r);
    return buf;
}

bool allClose(const double* a, const double* b, double atol) {
    for (int i = 0; i < 3; ++i)
        if (!(std::fabs(a[i] - b[i]) <= atol + 1e-5 * std::fabs(b[i]))) return false;
    return true;
}

} // namespace

std::vector<Snap> resolveSnaps(const std::vector<RawSnap>& raw) {
    std::vector<Snap> all;
    all.reserve(raw.size());
    for (const RawSnap& r : raw)
        if (auto s = transformSnap(*r.def, r.acc, true)) all.push_back(std::move(*s));

    // dedupe: same kind/gender/group/shape and position (key), then same position and
    // axis within tolerance. Rotation about the axis is ignored.
    std::map<std::string, std::vector<size_t>> buckets;
    std::vector<Snap> out;
    for (Snap& s : all) {
        std::string key;
        key += static_cast<char>('0' + s.kind);
        key += s.gender;
        key += std::to_string(s.group.size()) + ":" + s.group + "|";
        for (const Sec& sec : s.secs)
            key += std::string(secShapeName(sec.shape)) + "," + pyRoundKey(sec.radius, 3) + "," + pyRoundKey(sec.length, 3) + ";";
        key += "|" + pyRoundKey(s.radius, 3) + "|" + pyRoundKey(s.length, 3) + "|";
        for (int i = 0; i < 3; ++i) key += npRoundKey(s.matrix.t[i], 2) + ",";

        const double pos[3] = {s.matrix.t[0], s.matrix.t[1], s.matrix.t[2]};
        const double yax[3] = {s.matrix.r[0][1], s.matrix.r[1][1], s.matrix.r[2][1]};
        auto& prev = buckets[key];
        bool dup = false;
        for (size_t idx : prev) {
            const Snap& o = out[idx];
            const double opos[3] = {o.matrix.t[0], o.matrix.t[1], o.matrix.t[2]};
            const double oyax[3] = {o.matrix.r[0][1], o.matrix.r[1][1], o.matrix.r[2][1]};
            if (allClose(pos, opos, DEDUPE_TOL) && allClose(yax, oyax, DEDUPE_TOL)) { dup = true; break; }
        }
        if (dup) continue;
        prev.push_back(out.size());
        out.push_back(std::move(s));
    }
    return out;
}

} // namespace ldc
