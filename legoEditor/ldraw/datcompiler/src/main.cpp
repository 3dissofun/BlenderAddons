// ldcompile: flattens LDraw .dat files into .lego blobs for the Blender addon.
//
//   ldcompile <dat-directory | file.dat> [options]
//
// Blobs go in a "blobs" folder next to the input directory, e.g.
//   ldraw/parts/3001.dat -> ldraw/blobs/3001.lego

#include "blob.hpp"
#include "library.hpp"
#include "parser.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

using namespace ldc;

namespace {

void usage() {
    std::cerr <<
        "usage: ldcompile <dat-directory | file.dat> [options]\n"
        "\n"
        "  --lib DIR       LDraw library root (default: searched upward from the input for p/ and parts/)\n"
        "  --out DIR       output folder (default: <input dir>/../blobs)\n"
        "  -r              include sub-directories, mirrored under the output folder\n"
        "  --weld D        vertex weld distance in LDU (default 0.05, 0 disables)\n"
        "  --cache-mb N    memory for cached sub-files (default 1024)\n"
        "  --ldr           also write a flattened .ldr beside each blob, to check in LDView\n"
        "  -q              quiet: no progress line\n";
}

bool hasDatExt(const fs::path& p) {
    std::string e = p.extension().string();
    for (char& c : e) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return e == ".dat";
}

void appendNum(std::string& s, double v) {
    if (std::fabs(v) < 5e-7) v = 0.0; // no "-0"
    char buf[64];
    int n = std::snprintf(buf, sizeof buf, "%.6f", v);
    while (n > 0 && buf[n - 1] == '0') --n;
    if (n > 0 && buf[n - 1] == '.') --n;
    s.append(buf, static_cast<size_t>(n));
}

bool writeLDraw(const fs::path& outPath, const Geometry& geom, const std::string& title) {
    std::string s;
    const FaceList& faces = geom.faces;
    s.reserve(faces.size() * 96 + geom.edges.size() * 48 + 256);
    s += "0 " + title + " (flattened)\n0 Name: " + outPath.filename().string() + "\n0 BFC CERTIFY CCW\n";
    char buf[32];
    for (const Face& f : faces) {
        s += (f.n == 3) ? "3 " : "4 ";
        if (f.flags & FACE_DIRECT_COLOUR) std::snprintf(buf, sizeof buf, "0x2%06X", f.colour);
        else std::snprintf(buf, sizeof buf, "%d", f.colour);
        s += buf;
        for (int k = 0; k < f.n; ++k)
            for (int a = 0; a < 3; ++a) { s += ' '; appendNum(s, f.p[k][a]); }
        s += '\n';
    }
    for (const Edge& e : geom.edges) {
        s += "2 24";
        for (int k = 0; k < 2; ++k)
            for (int a = 0; a < 3; ++a) { s += ' '; appendNum(s, e.p[k][a]); }
        s += '\n';
    }
    std::ofstream o(outPath, std::ios::binary);
    if (!o) return false;
    o.write(s.data(), static_cast<std::streamsize>(s.size()));
    return static_cast<bool>(o);
}

} // namespace

int main(int argc, char** argv) {
    fs::path input, libRoot, outDir;
    bool recursive = false, dumpLdr = false, quiet = false;
    double weld = 0.05;
    size_t cacheMb = 1024;

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> const char* {
            if (i + 1 >= argc) { std::cerr << a << " needs a value\n"; std::exit(1); }
            return argv[++i];
        };
        if (a == "--lib") libRoot = next();
        else if (a == "--out") outDir = next();
        else if (a == "-r") recursive = true;
        else if (a == "--weld") weld = std::atof(next());
        else if (a == "--cache-mb") cacheMb = static_cast<size_t>(std::atoll(next()));
        else if (a == "--ldr") dumpLdr = true;
        else if (a == "-q") quiet = true;
        else if (a == "-h" || a == "--help") { usage(); return 0; }
        else if (!a.empty() && a[0] == '-') { std::cerr << "unknown option " << a << "\n"; usage(); return 1; }
        else if (input.empty()) input = a;
        else { std::cerr << "unexpected argument " << a << "\n"; usage(); return 1; }
    }
    if (input.empty()) { usage(); return 1; }

    std::error_code ec;
    input = fs::absolute(input, ec).lexically_normal();
    if (!input.empty() && !input.has_filename()) input = input.parent_path(); // strip trailing slash

    const bool singleFile = fs::is_regular_file(input, ec);
    if (!singleFile && !fs::is_directory(input, ec)) {
        std::cerr << "input not found: " << input << "\n";
        return 1;
    }
    const fs::path inDir = singleFile ? input.parent_path() : input;

    if (libRoot.empty()) {
        auto found = Library::findRoot(inDir);
        if (!found) {
            std::cerr << "could not find an LDraw library (a folder with p/ and parts/) above " << inDir
                      << "\npass it with --lib\n";
            return 1;
        }
        libRoot = *found;
    }
    if (outDir.empty()) outDir = inDir.parent_path() / "blobs";

    // Collect inputs
    std::vector<fs::path> files;
    if (singleFile) {
        files.push_back(input);
    } else if (recursive) {
        for (fs::recursive_directory_iterator it(inDir, fs::directory_options::skip_permission_denied, ec), end;
             !ec && it != end; it.increment(ec))
            if (it->is_regular_file(ec) && hasDatExt(it->path())) files.push_back(it->path());
    } else {
        for (fs::directory_iterator it(inDir, ec), end; !ec && it != end; it.increment(ec))
            if (it->is_regular_file(ec) && hasDatExt(it->path())) files.push_back(it->path());
    }
    std::sort(files.begin(), files.end());
    if (files.empty()) {
        std::cerr << "no .dat files in " << inDir << "\n";
        return 1;
    }

    const auto t0 = std::chrono::steady_clock::now();
    Library lib(libRoot);
    lib.setLocalDir(inDir);
    const auto t1 = std::chrono::steady_clock::now();

    std::cerr << "library " << lib.root().string() << " (" << lib.size() << " files)\n"
              << "writing " << files.size() << " blobs to " << outDir.string() << "\n";

    Flattener flat(lib, cacheMb * 1024 * 1024);
    size_t failed = 0, empty = 0, totalFaces = 0, totalDropped = 0;
    size_t edgeLines = 0, edgesDirect = 0, edgesWalked = 0, edgesUnmatched = 0, sharpEdges = 0, quadsSplit = 0;

    for (size_t i = 0; i < files.size(); ++i) {
        const fs::path& file = files[i];

        // Output name: relative path, lowercased, .dat -> .lego
        std::string rel = normalisePath(file.lexically_relative(inDir).generic_string());
        rel.replace(rel.size() - 4, 4, ".lego");
        const fs::path out = outDir / fs::path(rel);

        const auto geom = flat.flatten(file);
        BlobStats bs;
        if (!writeBlob(out, *geom, flat.textures(), weld, &bs)) {
            std::cerr << "\nERROR, could not write " << out << "\n";
            ++failed;
            continue;
        }
        if (bs.faces == 0) ++empty;
        totalFaces += bs.faces;
        totalDropped += bs.droppedFaces;
        edgeLines += bs.edgeLines;
        edgesDirect += bs.edgesDirect;
        edgesWalked += bs.edgesWalked;
        edgesUnmatched += bs.edgesUnmatched;
        sharpEdges += bs.sharpEdges;
        quadsSplit += bs.quadsSplit;

        if (dumpLdr) {
            fs::path ldr = out;
            ldr.replace_extension(".ldr");
            writeLDraw(ldr, *geom, file.filename().string());
        }

        if (!quiet && (i % 200 == 0 || i + 1 == files.size()))
            std::cerr << "\r[" << (i + 1) << "/" << files.size() << "] " << file.filename().string()
                      << "                    " << std::flush;
    }
    if (!quiet) std::cerr << "\n";

    const auto t2 = std::chrono::steady_clock::now();
    auto sec = [](auto a, auto b) { return std::chrono::duration<double>(b - a).count(); };
    const auto& st = flat.stats();
    std::cerr << "done in " << sec(t0, t2) << " s (indexing " << sec(t0, t1) << " s)\n"
              << "  " << (files.size() - failed) << " blobs written, " << failed << " failed, "
              << empty << " with no geometry\n"
              << "  " << totalFaces << " faces, " << totalDropped << " degenerate faces dropped by welding\n"
              << "  " << edgeLines << " edge lines: " << edgesDirect << " matched directly, " << edgesWalked
              << " across T-junctions, " << edgesUnmatched << " unmatched -> " << sharpEdges << " sharp mesh edges, "
              << quadsSplit << " quads split on a marked diagonal\n"
              << "  " << st.filesParsed << " files parsed, " << st.cacheHits << " cache hits, "
              << st.cacheEvictions << " evictions\n"
              << "  " << st.missingRefs << " missing refs, " << st.missingTextures << " missing textures, "
              << st.badLines << " bad lines\n";
    return failed ? 2 : 0;
}
