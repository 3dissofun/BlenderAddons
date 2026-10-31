"""LEGO element ID -> LDraw part number + LDraw colour code.

An element ID (the 6-7 digit number in instruction booklet part lists, on
Pick a Brick and on bags) is one design in one colour: 300121 is a 2x4 brick
(design 3001) in Bright Red (LEGO colour 21), which in LDraw is 3001.dat in
colour 4.

Pure Python, no bpy, so it works from Blender, from the OCR server thread and
from a plain command line:

    python mapping.py 300121 4211388 6058233
    python mapping.py --download            # fetch Rebrickable's elements.csv.gz

Sources, tried in this order (first hit wins):
  1. overrides.json   your own fixes, {"6058233": {"part": "3023", "colour": 4}}
  2. cache.json       earlier Rebrickable API answers
  3. elements.csv.gz  Rebrickable's free bulk download (element -> part, colour, design)
  4. Rebrickable API  only if an API key is set; answers are cached
  5. old-style guess  pre-2000s element IDs are just design ID + LEGO colour,
                      checked against the LDD design/colour tables in ldraw.xml

Every result carries a "source" and a list of part "candidates" (best first),
so the caller can pick the first one that actually exists in its LDraw library.
"""
import csv
import gzip
import json
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
LDRAW_XML = HERE / "ldraw.xml"
LDRAW_COLMAP = HERE.parent / "ldraw" / "colMap.json"   # LDraw colour codes (built from LDConfig.ldr)

ELEMENTS_URL = "https://cdn.rebrickable.com/media/downloads/elements.csv.gz"
API_ROOT = "https://rebrickable.com/api/v3/lego"
API_MIN_INTERVAL = 1.1      # seconds between API calls (Rebrickable allows ~1/s)
ELEMENT_RE = re.compile(r"^\d{4,8}$")

# Result "source" values, roughly most to least trustworthy
SRC_OVERRIDE, SRC_CACHE, SRC_DB, SRC_API, SRC_GUESS, SRC_MISSING = (
    "override", "cache", "db", "api", "guess", "missing")


def _stripDat(name):
    name = (name or "").strip()
    return name[:-4] if name.lower().endswith(".dat") else name


def defaultDataDir():
    # ~/.legoEditor outside Blender; the Blender side passes its own folder
    return Path.home() / ".legoEditor"


class ElementMapper:
    def __init__(self, dataDir=None, apiKey="", ldrawXml=None, colMapFile=None):
        self.dataDir = Path(dataDir) if dataDir else defaultDataDir()
        self.dataDir.mkdir(parents=True, exist_ok=True)
        self.apiKey = (apiKey or "").strip()
        self.ldrawXml = Path(ldrawXml) if ldrawXml else LDRAW_XML
        self.colMapFile = Path(colMapFile) if colMapFile else LDRAW_COLMAP

        self._lock = threading.RLock()
        self._apiLock = threading.Lock()
        self._lastApiCall = 0.0

        self._lddBricks = None      # LDD design id -> LDraw part (no .dat)
        self._lddColours = None     # LEGO colour id -> LDraw colour code
        self._knownParts = None     # LDraw part names named anywhere in ldraw.xml
        self._ldrawCodes = None     # set of valid LDraw colour codes, or None if unknown
        self._db = None             # element id -> (part_num, rb colour id, design id)
        self._rbColours = None      # Rebrickable colour id -> LDraw code
        self._overrides = None
        self._cache = None

    # ------------------------------------------------------------------ files
    @property
    def elementsFile(self):
        return self.dataDir / "elements.csv.gz"

    @property
    def overridesFile(self):
        return self.dataDir / "overrides.json"

    @property
    def cacheFile(self):
        return self.dataDir / "cache.json"

    @property
    def rbColoursFile(self):
        return self.dataDir / "rbColours.json"

    def hasDatabase(self):
        return self.elementsFile.is_file() or (self.dataDir / "elements.csv").is_file()

    @staticmethod
    def _readJson(path, default):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def _writeJson(path, data):
        tmp = Path(path).with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------ loaders
    def _loadLdd(self):
        if self._lddBricks is not None:
            return
        bricks, colours, known = {}, {}, set()
        if self.ldrawXml.is_file():
            root = ET.parse(str(self.ldrawXml)).getroot()
            # LDD -> LDraw direction: the LAST entry for a lego id wins (see ldraw.xml header)
            for b in root.iter("Brick"):
                lego, ldraw = b.get("lego"), b.get("ldraw")
                if lego and ldraw:
                    bricks[lego.strip()] = _stripDat(ldraw)
                    known.add(_stripDat(ldraw).lower())
            for t in root.iter("Transformation"):
                if t.get("ldraw"):
                    known.add(_stripDat(t.get("ldraw")).lower())
            for m in root.iter("Material"):
                try:
                    colours[int(m.get("lego"))] = int(m.get("ldraw"))
                except (TypeError, ValueError):
                    pass
        else:
            print(f"ELEMENT MAP: WARNING, ldraw.xml not found at '{self.ldrawXml}'")
        self._lddBricks, self._lddColours, self._knownParts = bricks, colours, known

    def _loadLdrawCodes(self):
        if self._ldrawCodes is not None:
            return self._ldrawCodes
        data = self._readJson(self.colMapFile, None)
        self._ldrawCodes = {int(k) for k in data} if data else set()
        return self._ldrawCodes

    def _loadDb(self):
        if self._db is not None:
            return self._db
        db = {}
        path = self.elementsFile if self.elementsFile.is_file() else self.dataDir / "elements.csv"
        if path.is_file():
            t0 = time.perf_counter()
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    eid = (row.get("element_id") or "").strip()
                    if not eid:
                        continue
                    try:
                        col = int(row.get("color_id") or -1)
                    except ValueError:
                        col = -1
                    db[eid] = ((row.get("part_num") or "").strip(), col,
                               (row.get("design_id") or "").strip())
            print(f"ELEMENT MAP: {len(db)} elements loaded from '{path.name}' "
                  f"in {time.perf_counter() - t0:.2f}s")
        self._db = db
        return db

    def _loadOverrides(self):
        if self._overrides is None:
            self._overrides = self._readJson(self.overridesFile, {})
        return self._overrides

    def _loadCache(self):
        if self._cache is None:
            self._cache = self._readJson(self.cacheFile, {})
        return self._cache

    def reload(self):
        # Forget everything loaded from disk (after a download or an edit)
        with self._lock:
            self._db = self._overrides = self._cache = self._rbColours = None
            self._ldrawCodes = None

    # ------------------------------------------------------------ public API
    def setOverride(self, elementId, part, colour):
        with self._lock:
            ov = self._loadOverrides()
            ov[str(elementId)] = {"part": _stripDat(part), "colour": int(colour)}
            self._writeJson(self.overridesFile, ov)

    def lookupMany(self, elementIds):
        return [self.lookup(e) for e in elementIds]

    def lookup(self, elementId):
        """Returns a dict:
            elementId, part (best LDraw part, no .dat, or ""), colour (LDraw code or None),
            candidates [parts, best first], name, source, note
        """
        eid = re.sub(r"\D", "", str(elementId))
        res = {"elementId": eid, "part": "", "colour": None, "candidates": [],
               "name": "", "source": SRC_MISSING, "note": ""}
        if not ELEMENT_RE.match(eid):
            res["note"] = "not an element ID"
            return res

        with self._lock:
            self._loadLdd()

            hit = self._loadOverrides().get(eid)
            if hit:
                return self._finish(res, [hit.get("part", "")], hit.get("colour"),
                                    hit.get("name", ""), SRC_OVERRIDE)

            hit = self._loadCache().get(eid)
            if hit:
                return self._finish(res, hit.get("candidates") or [hit.get("part", "")],
                                    hit.get("colour"), hit.get("name", ""), SRC_CACHE, hit.get("note", ""))

            row = self._loadDb().get(eid)

        if row:
            partNum, rbCol, design = row
            colour, note = self._rbToLdrawColour(rbCol)
            return self._finish(res, self._partCandidates(partNum, design), colour, "", SRC_DB, note)

        if self.apiKey:
            try:
                apiRes = self._apiLookup(eid)
            except Exception as e:     # network errors must never break a scan
                apiRes = None
                res["note"] = f"API error: {e}"
            if apiRes:
                with self._lock:
                    cache = self._loadCache()
                    cache[eid] = apiRes
                    self._writeJson(self.cacheFile, cache)
                return self._finish(res, apiRes["candidates"], apiRes["colour"],
                                    apiRes["name"], SRC_API, apiRes.get("note", ""))

        guess = self._oldStyleGuess(eid)
        if guess:
            parts, colour = guess
            return self._finish(res, parts, colour, "", SRC_GUESS,
                                "guessed from the old design+colour numbering")

        if not res["note"]:
            res["note"] = ("unknown element; download the element database"
                           if not self.hasDatabase() else "unknown element")
        return res

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _finish(res, candidates, colour, name, source, note=""):
        seen, cands = set(), []
        for c in candidates:
            c = _stripDat(c)
            if c and c.lower() not in seen:
                seen.add(c.lower())
                cands.append(c)
        res.update(candidates=cands, part=cands[0] if cands else "",
                   colour=None if colour is None else int(colour),
                   name=name or "", source=source if cands else SRC_MISSING)
        if note:
            res["note"] = (res["note"] + "; " + note).strip("; ")
        return res

    def _partCandidates(self, partNum, design):
        # LDraw part names, best first. ldraw.xml (LDD design -> LDraw) beats
        # Rebrickable's own numbering, which is close to LDraw's but not identical.
        out = []
        if design and design in self._lddBricks:
            out.append(self._lddBricks[design])
        if partNum:
            out.append(partNum)
            if partNum in self._lddBricks:
                out.append(self._lddBricks[partNum])
            base = re.match(r"^(\d+)[a-z]$", partNum)   # 3069b -> 3069
            if base:
                out.append(base.group(1))
        if design:
            out.append(design)
        return out

    def _rbToLdrawColour(self, rbCol):
        if rbCol is None or rbCol < 0:
            return None, "no colour"
        rb = self._rbColourMap()
        if rb is not None and str(rbCol) in rb:
            return rb[str(rbCol)], ""
        # Rebrickable's colour ids started out as LDraw codes and most common
        # ones still match (0 black, 4 red, 15 white, 71/72 greys...)
        codes = self._loadLdrawCodes()
        if not codes or rbCol in codes:
            return rbCol, "" if rb is not None else "colour assumed equal to Rebrickable id"
        return None, f"Rebrickable colour {rbCol} has no LDraw match"

    def _rbColourMap(self):
        # Rebrickable colour -> LDraw, via the API's external ids. Fetched once, cached.
        if self._rbColours is not None:
            return self._rbColours
        data = self._readJson(self.rbColoursFile, None)
        if data is None and self.apiKey:
            try:
                data = {}
                body = self._apiGet(f"{API_ROOT}/colors/?page_size=1000")
                for c in body.get("results", []):
                    ids = (((c.get("external_ids") or {}).get("LDraw") or {}).get("ext_ids") or [])
                    if ids:
                        data[str(c["id"])] = int(ids[0])
                self._writeJson(self.rbColoursFile, data)
            except Exception as e:
                print(f"ELEMENT MAP: WARNING, colour table download failed: {e}")
                data = None
        self._rbColours = data
        return data

    def _oldStyleGuess(self, eid):
        # 300121 -> design 3001 + colour 21. Only trusted if the colour is a known
        # LDD colour and the design is a part ldraw.xml knows about.
        # Classic designs are 4 digits (3001, 3023), so try that split first,
        # then 5-digit designs, then 3-digit ones.
        best = None
        for split in (4, 5, 3):
            if split >= len(eid):
                continue
            design, col = eid[:split], eid[split:]
            if (col.startswith("0") and len(col) != 2) or len(col) > 3:   # "01" = colour 1
                continue
            ldColour = self._lddColours.get(int(col))
            if ldColour is None:
                continue
            if design in self._lddBricks or design.lower() in self._knownParts:
                return self._partCandidates("", design), ldColour
            if best is None and split == 4:
                best = (self._partCandidates("", design), ldColour)
        return best

    # ------------------------------------------------------------ Rebrickable API
    def _apiGet(self, url):
        with self._apiLock:
            wait = API_MIN_INTERVAL - (time.monotonic() - self._lastApiCall)
            if wait > 0:
                time.sleep(wait)
            req = urllib.request.Request(url, headers={
                "Authorization": f"key {self.apiKey}", "Accept": "application/json",
                "User-Agent": "legoEditor-blender"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return json.loads(r.read().decode("utf-8"))
            finally:
                self._lastApiCall = time.monotonic()

    def _apiLookup(self, eid):
        try:
            body = self._apiGet(f"{API_ROOT}/elements/{eid}/")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        part = body.get("part") or {}
        color = body.get("color") or {}
        design = str(body.get("design_id") or "")
        self._loadLdd()
        cands = list((part.get("external_ids") or {}).get("LDraw") or [])
        cands += self._partCandidates(part.get("part_num", ""), design)
        ldIds = (((color.get("external_ids") or {}).get("LDraw") or {}).get("ext_ids") or [])
        colour, note = (int(ldIds[0]), "") if ldIds else self._rbToLdrawColour(color.get("id", -1))
        return {"candidates": [_stripDat(c) for c in cands], "colour": colour,
                "name": f"{part.get('name', '')} ({color.get('name', '')})".strip(" ()"),
                "note": note}

    # ------------------------------------------------------------ download
    def downloadDatabase(self, progress=None):
        """Fetch Rebrickable's elements.csv.gz (a few MB, no API key needed)."""
        req = urllib.request.Request(ELEMENTS_URL, headers={"User-Agent": "legoEditor-blender"})
        tmp = self.elementsFile.with_suffix(".part")
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        # sanity check before replacing a working file
        with gzip.open(tmp, "rt", encoding="utf-8") as fh:
            header = fh.readline()
        if "element_id" not in header:
            tmp.unlink(missing_ok=True)
            raise ValueError("downloaded file is not Rebrickable's elements.csv")
        tmp.replace(self.elementsFile)
        self.reload()
        return self.elementsFile


# ---------------------------------------------------------------------------
# Text helpers shared with the OCR server
# ---------------------------------------------------------------------------

ID_IN_TEXT = re.compile(r"(?<!\d)(\d{6,7})(?!\d)")


def findElementIds(text):
    # Ordered, de-duplicated 6-7 digit runs in a block of OCR text
    seen, out = set(), []
    for m in ID_IN_TEXT.finditer(text or ""):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Map LEGO element IDs to LDraw parts")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--data", help="data folder (default ~/.legoEditor)")
    ap.add_argument("--key", default="", help="Rebrickable API key (optional)")
    ap.add_argument("--download", action="store_true", help="download elements.csv.gz")
    a = ap.parse_args()
    m = ElementMapper(a.data, a.key)
    if a.download:
        print("saved", m.downloadDatabase(lambda d, t: print(f"\r{d >> 10} KB", end="")))
    for e in a.ids:
        r = m.lookup(e)
        print(f"{r['elementId']:>8} -> {r['part'] or '?':<10} colour {r['colour']!s:<5} "
              f"[{r['source']}] {r['name']} {r['note']}")
