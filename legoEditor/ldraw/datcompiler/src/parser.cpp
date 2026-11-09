#include "parser.hpp"

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string_view>

namespace ldc {

namespace {

bool readFile(const fs::path& p, std::string& out) {
    std::ifstream f(p, std::ios::binary);
    if (!f) return false;
    f.seekg(0, std::ios::end);
    const std::streamoff size = f.tellg();
    f.seekg(0, std::ios::beg);
    out.resize(static_cast<size_t>(size));
    if (size > 0) f.read(out.data(), size);

    // Strip a UTF-8 BOM (matches encoding="utf-8-sig" in the Python parser)
    if (out.size() >= 3 && static_cast<unsigned char>(out[0]) == 0xEF &&
        static_cast<unsigned char>(out[1]) == 0xBB && static_cast<unsigned char>(out[2]) == 0xBF) {
        out.erase(0, 3);
    }
    return true;
}

bool isBlank(char c) { return c == ' ' || c == '\t'; }

void tokenize(std::string_view line, std::vector<std::string_view>& toks) {
    toks.clear();
    size_t i = 0;
    const size_t n = line.size();
    while (i < n) {
        while (i < n && isBlank(line[i])) ++i;
        if (i >= n) break;
        const size_t s = i;
        while (i < n && !isBlank(line[i])) ++i;
        toks.push_back(line.substr(s, i - s));
    }
}

// Like shlex.split(posix=False): a double-quoted run is one token, quotes kept.
void tokenizeQuoted(std::string_view line, std::vector<std::string_view>& toks) {
    toks.clear();
    size_t i = 0;
    const size_t n = line.size();
    while (i < n) {
        while (i < n && isBlank(line[i])) ++i;
        if (i >= n) break;
        const size_t s = i;
        bool inQuote = false;
        while (i < n && (inQuote || !isBlank(line[i]))) {
            if (line[i] == '"') inQuote = !inQuote;
            ++i;
        }
        toks.push_back(line.substr(s, i - s));
    }
}

bool parseDouble(std::string_view s, double& out) {
    if (!s.empty() && s[0] == '+') s.remove_prefix(1); // from_chars rejects a leading '+'
    const auto r = std::from_chars(s.data(), s.data() + s.size(), out);
    return r.ec == std::errc() && r.ptr == s.data() + s.size();
}

// "4" -> code 4. "0x2RRGGBB" / "#2RRGGBB" -> direct colour 0xRRGGBB.
bool parseColour(std::string_view s, Colour& out) {
    std::string_view hex;
    if (s.size() > 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) hex = s.substr(2);
    else if (s.size() > 1 && s[0] == '#') hex = s.substr(1);

    if (!hex.empty()) {
        uint32_t v = 0;
        const auto r = std::from_chars(hex.data(), hex.data() + hex.size(), v, 16);
        if (r.ec != std::errc() || r.ptr != hex.data() + hex.size()) return false;
        if ((v >> 24) != 0x2) return false; // only 0x2RRGGBB is supported (not dithered 0x3)
        out = {static_cast<int32_t>(v & 0xFFFFFF), true};
        return true;
    }

    int32_t v = 0;
    const auto r = std::from_chars(s.data(), s.data() + s.size(), v);
    if (r.ec != std::errc() || r.ptr != s.data() + s.size()) return false;
    out = {v, false};
    return true;
}

std::string upper(std::string_view s) {
    std::string o(s);
    for (char& c : o) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return o;
}

void warnLine(const fs::path& file, size_t lineNo, const std::string& msg) {
    std::cerr << "PARSER: WARNING, " << msg << " (" << file.filename().string() << ":" << lineNo << ")\n";
}

// Apply a texmap to a face. pts are the face's positions in double precision, in the
// texmap's space and in the face's (final) corner order. Doubles matter: points that
// lie on a spherical/cylindrical seam can flip side if rounded to float first.
void applyTex(Face& f, const Vec3* pts, const TexMap& t) {
    if (computeUV(t, pts, f.n, f.uv)) {
        f.tex = t.tex;
        f.flags |= FACE_TEXTURED;
    } else {
        std::memset(f.uv, 0, sizeof f.uv);
        f.tex = -1;
        f.flags &= static_cast<uint8_t>(~FACE_TEXTURED);
    }
    f.flags |= FACE_TEX_RESOLVED;
}

} // namespace

Flattener::Flattener(const Library& lib, size_t cacheBudgetBytes)
    : lib_(lib), cacheBudget_(cacheBudgetBytes) {}

// ---------------------------------------------------------------------------
// Cache
// ---------------------------------------------------------------------------

std::shared_ptr<const Geometry> Flattener::cacheGet(const std::string& key) {
    auto it = cache_.find(key);
    if (it == cache_.end()) return nullptr;
    lru_.splice(lru_.begin(), lru_, it->second.lruIt);
    return it->second.geom;
}

void Flattener::cachePut(const std::string& key, std::shared_ptr<const Geometry> geom) {
    const size_t bytes = geom->faces.size() * sizeof(Face) + geom->edges.size() * sizeof(Edge) + key.size() + 64;
    lru_.push_front(key);
    cache_[key] = {std::move(geom), lru_.begin(), bytes};
    cacheBytes_ += bytes;

    // Evict least recently used, never the entry just added
    while (cacheBytes_ > cacheBudget_ && lru_.size() > 1) {
        const std::string& victim = lru_.back();
        auto it = cache_.find(victim);
        cacheBytes_ -= it->second.bytes;
        cache_.erase(it);
        lru_.pop_back();
        ++stats_.cacheEvictions;
    }
}

std::shared_ptr<const Geometry> Flattener::flatten(const fs::path& file) {
    static const auto empty = std::make_shared<const Geometry>();

    const std::string key = file.lexically_normal().string();
    if (auto hit = cacheGet(key)) {
        ++stats_.cacheHits;
        return hit;
    }
    if (!inProgress_.insert(key).second) {
        std::cerr << "PARSER: WARNING, circular reference to " << file << ", skipped\n";
        return empty;
    }

    auto geom = std::make_shared<Geometry>();
    parseInto(file, *geom);
    geom->faces.shrink_to_fit();
    geom->edges.shrink_to_fit();

    inProgress_.erase(key);
    ++stats_.filesParsed;
    std::shared_ptr<const Geometry> result = geom;
    cachePut(key, result);
    return result;
}

// ---------------------------------------------------------------------------
// Textures
// ---------------------------------------------------------------------------

int32_t Flattener::registerTexture(const std::string& tex, const std::string& gloss) {
    const std::string key = tex + '\n' + gloss;
    if (auto it = textureIndex_.find(key); it != textureIndex_.end()) return it->second;
    const auto idx = static_cast<int32_t>(textures_.size());
    textures_.push_back({tex, gloss});
    textureIndex_.emplace(key, idx);
    return idx;
}

// args[start] is the projection method, followed by its parameters and texture name(s)
std::optional<TexMap> Flattener::parseTexmap(const std::vector<std::string_view>& args, size_t start,
                                             const fs::path& file, size_t lineNo) {
    if (args.size() <= start) {
        warnLine(file, lineNo, "TEXMAP missing projection type");
        return std::nullopt;
    }

    TexMap t{};
    const std::string method = upper(args[start]);
    size_t nParams;
    if (method == "PLANAR") { t.type = TexMap::PLANAR; nParams = 9; }
    else if (method == "CYLINDRICAL") { t.type = TexMap::CYLINDRICAL; nParams = 10; }
    else if (method == "SPHERICAL") { t.type = TexMap::SPHERICAL; nParams = 11; }
    else {
        warnLine(file, lineNo, "unrecognised TEXMAP projection '" + std::string(args[start]) + "'");
        return std::nullopt;
    }

    if (args.size() < start + 1 + nParams + 1) {
        warnLine(file, lineNo, "TEXMAP " + method + " has too few arguments");
        return std::nullopt;
    }
    for (size_t i = 0; i < nParams; ++i) {
        if (!parseDouble(args[start + 1 + i], t.prm[i])) {
            warnLine(file, lineNo, "TEXMAP " + method + " has non-numeric parameters");
            return std::nullopt;
        }
    }

    const size_t rest = start + 1 + nParams;
    const fs::path fileDir = file.parent_path();

    const auto texPath = lib_.resolveTexture(args[rest], fileDir);
    if (!texPath) {
        ++stats_.missingTextures;
        warnLine(file, lineNo, "texture could not be resolved: " + std::string(args[rest]));
        return std::nullopt; // main texture missing: faces stay untextured
    }

    std::string gloss;
    if (args.size() >= rest + 3 && upper(args[rest + 1]) == "GLOSSMAP") {
        if (auto g = lib_.resolveTexture(args[rest + 2], fileDir)) gloss = lib_.storedPath(*g);
        else warnLine(file, lineNo, "glossmap could not be resolved: " + std::string(args[rest + 2]));
    }

    t.tex = registerTexture(lib_.storedPath(*texPath), gloss);
    return t;
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

void Flattener::parseInto(const fs::path& file, Geometry& out) {
    std::string text;
    if (!readFile(file, text)) {
        std::cerr << "PARSER: WARNING, could not open " << file << "\n";
        return;
    }

    // BFC state
    bool cw = false;          // this file's own faces are clockwise
    bool invertNext = false;  // next type 1 is inverted

    // TEXMAP state. nullopt entries on the stack are texmaps whose texture was
    // missing: they texture nothing, so a parent's texmap still applies.
    std::vector<std::optional<TexMap>> texStack;
    std::optional<TexMap> nextTex;  // set by NEXT, applies to the next line only
    bool inFallback = false;

    std::vector<std::string_view> tok;
    tok.reserve(32);

    const std::string_view all(text);
    size_t pos = 0;
    size_t lineNo = 0;

    while (pos < all.size()) {
        size_t nl = all.find('\n', pos);
        if (nl == std::string_view::npos) nl = all.size();
        std::string_view line = all.substr(pos, nl - pos);
        pos = nl + 1;
        ++lineNo;

        if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
        tokenize(line, tok);
        if (tok.empty()) continue;

        std::optional<TexMap> pendingNext = std::move(nextTex);
        nextTex.reset();

        // Inside a FALLBACK block: skip everything up to the matching END
        if (inFallback && !(tok.size() >= 3 && tok[0] == "0" && tok[1] == "!TEXMAP" && tok[2] == "END"))
            continue;

        // "0 !: <line>" is texmap-only geometry: unwrap and treat as a normal line
        if (tok[0] == "0" && tok.size() >= 2 && tok[1] == "!:") {
            if (tok.size() < 3) continue;
            line = line.substr(static_cast<size_t>(tok[2].data() - line.data()));
            tokenize(line, tok);
        }

        if (tok[0].size() != 1 || tok[0][0] < '0' || tok[0][0] > '5') {
            warnLine(file, lineNo, "unknown line type");
            ++stats_.badLines;
            continue;
        }
        const int type = tok[0][0] - '0';

        const TexMap* active = pendingNext ? &*pendingNext
                             : (!texStack.empty() && texStack.back()) ? &*texStack.back()
                             : nullptr;

        switch (type) {
        case 0: {
            if (tok.size() < 2) break;

            if (tok[1] == "BFC") {
                for (size_t i = 2; i < tok.size(); ++i) {
                    if (tok[i] == "INVERTNEXT") invertNext = true;
                    else if (tok[i] == "CW") cw = true;
                    else if (tok[i] == "CCW") cw = false;
                }
            } else if (tok[1] == "!TEXMAP") {
                std::vector<std::string_view> targs;
                tokenizeQuoted(line, targs);   // texture names may be quoted with spaces
                if (targs.size() < 3) break;
                const std::string cmd = upper(targs[2]);
                if (cmd == "START") texStack.push_back(parseTexmap(targs, 3, file, lineNo));
                else if (cmd == "NEXT") nextTex = parseTexmap(targs, 3, file, lineNo);
                else if (cmd == "FALLBACK") inFallback = true;
                else if (cmd == "END") {
                    if (!texStack.empty()) texStack.pop_back(); // END without START has no effect
                    inFallback = false;
                }
            } else if (tok[1] == "STEP") {
                texStack.clear();
            }
            break;
        }

        case 1: {
            if (tok.size() < 15) {
                warnLine(file, lineNo, "type 1 line has too few fields");
                ++stats_.badLines;
                invertNext = false;
                break;
            }

            Colour colour;
            double v[12];
            bool ok = parseColour(tok[1], colour);
            for (int i = 0; ok && i < 12; ++i) ok = parseDouble(tok[2 + i], v[i]);
            if (!ok) {
                warnLine(file, lineNo, "bad number or colour in type 1 line");
                ++stats_.badLines;
                invertNext = false;
                break;
            }

            const Mat34 m{
                {{v[3], v[4], v[5]}, {v[6], v[7], v[8]}, {v[9], v[10], v[11]}},
                {v[0], v[1], v[2]},
            };

            // File name is the rest of the line, so names with spaces survive
            const std::string_view name = line.substr(static_cast<size_t>(tok[14].data() - line.data()));

            const auto childPath = lib_.resolve(name);
            if (!childPath) {
                ++stats_.missingRefs;
                std::string n = normalisePath(name);
                if (warnedMissing_.insert(n).second)
                    std::cerr << "PARSER: WARNING, part could not be found: '" << n << "'\n";
                invertNext = false;
                break;
            }

            const auto child = flatten(*childPath);

            // Child faces are CCW in child space. They flip if this reference is
            // inverted or the matrix mirrors (negative determinant).
            const bool reverse = invertNext ^ (m.det() < 0.0);

            out.faces.reserve(out.faces.size() + child->faces.size());
            for (const Face& cf : child->faces) {
                Face f = cf;
                if (cf.colour == 16 && !(cf.flags & FACE_DIRECT_COLOUR)) {
                    f.colour = colour.value;
                    if (colour.direct) f.flags |= FACE_DIRECT_COLOUR;
                }
                Vec3 tp[4];
                for (int k = 0; k < cf.n; ++k) {
                    tp[k] = m.apply(cf.pt(k));
                    f.setPt(k, tp[k]);
                }
                if (reverse) {
                    f.reverse();
                    std::reverse(tp, tp + cf.n);
                }
                if (active && !(f.flags & FACE_TEX_RESOLVED)) applyTex(f, tp, *active);
                out.faces.push_back(f);
            }

            // Edge lines have no winding, just transform them
            out.edges.reserve(out.edges.size() + child->edges.size());
            for (const Edge& ce : child->edges) {
                Edge e;
                for (int k = 0; k < 2; ++k) {
                    const Vec3 q = m.apply({ce.p[k][0], ce.p[k][1], ce.p[k][2]});
                    e.p[k][0] = static_cast<float>(q.x);
                    e.p[k][1] = static_cast<float>(q.y);
                    e.p[k][2] = static_cast<float>(q.z);
                }
                out.edges.push_back(e);
            }

            invertNext = false;
            break;
        }

        case 3:
        case 4: {
            const int n = type;
            if (tok.size() < static_cast<size_t>(2 + 3 * n)) {
                warnLine(file, lineNo, "face line has too few fields");
                ++stats_.badLines;
                invertNext = false;
                break;
            }

            Face f{};
            f.n = static_cast<uint8_t>(n);
            f.tex = -1;
            Colour colour;
            Vec3 pts[4];
            bool ok = parseColour(tok[1], colour);
            for (int k = 0; ok && k < n; ++k) {
                ok = parseDouble(tok[2 + 3 * k], pts[k].x)
                  && parseDouble(tok[3 + 3 * k], pts[k].y)
                  && parseDouble(tok[4 + 3 * k], pts[k].z);
                f.setPt(k, pts[k]);
            }
            if (!ok) {
                warnLine(file, lineNo, "bad number or colour in face line");
                ++stats_.badLines;
                invertNext = false;
                break;
            }
            f.colour = colour.value;
            if (colour.direct) f.flags |= FACE_DIRECT_COLOUR;

            if (cw) { // store everything CCW
                f.reverse();
                std::reverse(pts, pts + n);
            }
            if (active) applyTex(f, pts, *active);
            out.faces.push_back(f);
            invertNext = false;
            break;
        }

        case 2: { // edge line: marks a hard edge
            if (tok.size() < 8) {
                warnLine(file, lineNo, "edge line has too few fields");
                ++stats_.badLines;
                invertNext = false;
                break;
            }
            Edge e;
            bool ok = true;
            for (int i = 0; ok && i < 6; ++i) {
                double d;
                ok = parseDouble(tok[2 + i], d);
                e.p[i / 3][i % 3] = static_cast<float>(d);
            }
            if (ok) out.edges.push_back(e);
            else { warnLine(file, lineNo, "bad number in edge line"); ++stats_.badLines; }
            invertNext = false;
            break;
        }

        default: // 5 (conditional lines) mark smooth silhouettes: not needed
            invertNext = false;
            break;
        }
    }
}

} // namespace ldc
