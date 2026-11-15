#include "snapfile.hpp"

#include <algorithm>
#include <cstring>
#include <fstream>

namespace ldc {

namespace {

class StringTable {
public:
    StringTable() { add(""); }

    uint32_t add(const std::string& s) {
        if (auto it = offsets_.find(s); it != offsets_.end()) return it->second;
        const auto off = static_cast<uint32_t>(bytes.size());
        const auto len = static_cast<uint16_t>(std::min<size_t>(s.size(), 0xFFFF));
        bytes.push_back(static_cast<char>(len & 0xFF));
        bytes.push_back(static_cast<char>(len >> 8));
        bytes.insert(bytes.end(), s.data(), s.data() + len);
        offsets_.emplace(s, off);
        return off;
    }

    std::vector<char> bytes;

private:
    std::unordered_map<std::string, uint32_t> offsets_;
};

std::string join(const std::vector<std::string>& v, char sep) {
    std::string o;
    for (size_t i = 0; i < v.size(); ++i) {
        if (i) o += sep;
        o += v[i];
    }
    return o;
}

void align16(std::vector<char>& buf) { buf.resize((buf.size() + 15) & ~static_cast<size_t>(15), 0); }

template <typename T>
uint32_t appendSection(std::vector<char>& buf, const T* data, size_t count) {
    if (count == 0) return 0;
    align16(buf);
    const auto off = static_cast<uint32_t>(buf.size());
    buf.resize(buf.size() + count * sizeof(T));
    std::memcpy(buf.data() + off, data, count * sizeof(T));
    return off;
}

} // namespace

bool writeSnapFile(const fs::path& out, const std::vector<Snap>& snaps) {
    StringTable strings;
    std::vector<SnapRecord> recs;
    std::vector<SecRecord> secs;
    recs.reserve(snaps.size());

    for (const Snap& s : snaps) {
        SnapRecord r{};
        for (int i = 0; i < 3; ++i) {
            for (int j = 0; j < 3; ++j) r.matrix[4 * i + j] = static_cast<float>(s.matrix.r[i][j]);
            r.matrix[4 * i + 3] = static_cast<float>(s.matrix.t[i]);
        }
        r.matrix[15] = 1.0f;
        r.radius = static_cast<float>(s.radius);
        r.length = static_cast<float>(s.length);
        r.secStart = static_cast<uint32_t>(secs.size());
        r.secCount = static_cast<uint32_t>(s.secs.size());
        for (const Sec& sec : s.secs)
            secs.push_back({static_cast<uint8_t>(sec.shape), {0, 0, 0}, static_cast<float>(sec.radius),
                            static_cast<float>(sec.length)});
        r.group = strings.add(s.group);
        r.ids = strings.add(join(s.ids, ','));
        r.bounding = strings.add(join(s.bounding, ' '));
        r.kind = static_cast<uint8_t>(s.kind);
        r.gender = static_cast<uint8_t>(s.gender);
        r.flags = static_cast<uint8_t>((s.capA ? SNAPREC_CAP_A : 0) | (s.capB ? SNAPREC_CAP_B : 0) |
                                       (s.center ? SNAPREC_CENTER : 0) | (s.slide ? SNAPREC_SLIDE : 0));
        recs.push_back(r);
    }

    SnapHeader h{};
    std::memcpy(h.magic, "SNAP", 4);
    h.version = SNAPFILE_VERSION;
    h.nSnaps = static_cast<uint32_t>(recs.size());
    h.nSecs = static_cast<uint32_t>(secs.size());

    std::vector<char> buf(sizeof(SnapHeader), 0);
    h.offSnaps = appendSection(buf, recs.data(), recs.size());
    h.offSecs = appendSection(buf, secs.data(), secs.size());
    h.offStrings = appendSection(buf, strings.bytes.data(), strings.bytes.size());
    h.stringBytes = static_cast<uint32_t>(strings.bytes.size());
    std::memcpy(buf.data(), &h, sizeof h);

    std::error_code ec;
    fs::create_directories(out.parent_path(), ec);
    std::ofstream o(out, std::ios::binary);
    if (!o) return false;
    o.write(buf.data(), static_cast<std::streamsize>(buf.size()));
    return static_cast<bool>(o);
}

} // namespace ldc
