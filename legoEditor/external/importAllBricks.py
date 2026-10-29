"""
Import every final LDraw brick into a library collection and save an index map of them.

Run from Blender's Text Editor (or `blender file.blend --python importBrickLibrary.py`)
with the Lego Editor addon enabled and its part library set in the addon preferences.

What it does
  1. Map:    scans the top level of parts/ and keeps the files that are final models
             (Part / Shortcut), dropping subparts, primitives, "~" helper parts,
             "~Moved to" stubs, aliases and physical-colour variants (configurable below).
  2. Import: parses the bricks on a thread pool with the addon's own parser, then
             builds each one on the main thread with importer.makeBrick (bpy data
             can only be created on the main thread). Bricks land in a collection
             that is excluded from the view layer.
  3. Save:   writes the {index: brickName} map to a text block in the .blend and,
             if MAP_FILE is set, to a JSON file.

Only bricks that imported with geometry get an index, so every index in the map has
a brick object in COLLECTION_NAME (tagged with its name in obj["lego_brick"]).

With USE_EXTERNAL_INDEX on, step 1 is replaced by reading EXTERNAL_INDEX_FILE (the
fixed-width .bin written by brickIndexBlob.py), so the order is exactly the one in
that file. Indices are never renumbered in this mode: a
brick that fails to import keeps its slot and is listed in the log, and the external
file is never overwritten.
"""

import bpy
import os
import sys
import json
import time
import shutil
import tempfile
import threading
import subprocess
import numpy as np
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

COLLECTION_NAME = "LEGO_BrickLibrary"
MAP_TEXT_NAME = "lego_brick_index.json"
LOG_TEXT_NAME = "lego_brick_import_log.txt"
MAP_FILE = ""  # "//" = next to the .blend; "" to skip writing a file

USE_EXTERNAL_INDEX = True                 # True: take brick order from EXTERNAL_INDEX_FILE
EXTERNAL_INDEX_FILE = r"C:\Users\Josh\Desktop\blenderPipeline\exampleProjectsDir\pipelineRepo\release\addons\legoEditor\ldrawLibrary\pieceIndex.bin"   # 64-byte records from brickIndexBlob.py

# Record layout, must match brickIndexBlob.py: uint32 index | uint32 chrAmount | 56-byte name
INDEX_RECORD = np.dtype([("index", "<u4"), ("chrAmount", "<u4"), ("name", "S56")])

INCLUDE_SHORTCUTS = True          # pre-assembled parts (e.g. hinge assemblies, minifig legs)
INCLUDE_ALIASES = False           # "=" parts that just reference another part number
INCLUDE_PHYSICAL_COLOUR = False   # "_" parts that are another part in a fixed colour
INCLUDE_FLEXIBLE_SECTIONS = False # hose / cable segments

PROGRESS_EVERY = 500   # print a progress line every N bricks imported
LIMIT = 0              # > 0 imports only the first N mapped bricks (handy for a test run)
WORKERS = max(1, os.cpu_count() or 4)
REUSE_EXISTING = True  # keep bricks already in COLLECTION_NAME instead of re-importing them

# Parallel import: run the import in several background Blender instances at once,
# then append their bricks back into COLLECTION_NAME in this file. Threads alone share
# one Python interpreter and build every mesh on the main thread; separate instances
# don't, so this scales with CPU cores. 0 or 1 = import in this Blender only.
PARALLEL_INSTANCES = max(1, (os.cpu_count() or 2) - 1)
WORKER_THREADS = 2     # parse threads inside each instance (overlaps file reads)
BLENDER_BINARY = ""    # "" = the Blender running this script

# ---------------------------------------------------------------------------
# Addon modules
# ---------------------------------------------------------------------------

def getAddonModules():
    # Works for both a legacy addon ("legoEditor") and an extension
    # ("bl_ext.user_default.legoEditor")
    hits = sorted(n for n in sys.modules if n.endswith(".ldraw.importer"))
    hits.sort(key=lambda n: "legoEditor" not in n)
    if not hits:
        raise RuntimeError("Lego Editor addon isn't loaded. Enable it in Preferences > Add-ons first.")
    pkg = hits[0].rpartition(".")[0]
    return sys.modules[pkg + ".importer"], sys.modules[pkg + ".library"], sys.modules[pkg + ".parser"]

importer, library, parser = getAddonModules()

# ---------------------------------------------------------------------------
# 1. Map final bricks
# ---------------------------------------------------------------------------

def progress(msg):
    # flush, so each line shows up in Blender's console while the script is still running
    print(f"LEGO LIBRARY: {msg}", flush=True)

def readHeader(path):
    # (description, LDRAW_ORG type, qualifier set) from the file's header block
    desc, orgType, quals = None, None, set()
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for lineNo, raw in enumerate(f):
                t = raw.split()
                if not t:
                    continue
                if t[0] != "0" or lineNo > 60:
                    break  # header is over once geometry/references start
                if desc is None:
                    desc = raw.strip()[1:].strip()
                elif len(t) > 2 and t[1] == "!LDRAW_ORG":
                    orgType = t[2].lower().removeprefix("unofficial_")
                    quals = {q.lower() for q in t[3:]}
                    break
    except OSError:
        return None, None, set()
    return desc or "", orgType, quals

def isFinalBrick(path):
    desc, orgType, quals = readHeader(path)
    if desc is None:
        return False, "unreadable"
    if desc.startswith("~"):
        return False, "helper / moved"  # "~Moved to ...", "~Brick 1 x 2 without ..." etc.
    if orgType is not None:
        if orgType == "shortcut":
            if not INCLUDE_SHORTCUTS:
                return False, "shortcut"
        elif orgType != "part":
            return False, orgType  # subpart, primitive, 8_primitive, 48_primitive ...
    if "alias" in quals or desc.startswith("="):
        if not INCLUDE_ALIASES:
            return False, "alias"
    if "physical_colour" in quals or desc.startswith("_"):
        if not INCLUDE_PHYSICAL_COLOUR:
            return False, "physical colour"
    if "flexible_section" in quals and not INCLUDE_FLEXIBLE_SECTIONS:
        return False, "flexible section"
    return True, ""

def mapFinalBricks(log):
    # Ordered list of brick names ("3001", "3626cp01" ...), sorted as in library.getAllParts()
    keys = library.getAllParts()  # top level of parts/, already excludes parts/s
    progress(f"Scanning {len(keys)} part headers...")
    paths = [library.partIndexLookup(k) for k in keys]
    with ThreadPoolExecutor(WORKERS) as pool:
        verdicts = list(pool.map(lambda p: isFinalBrick(p) if p else (False, "missing"), paths))

    names, skipped = [], {}
    for key, (ok, why) in zip(keys, verdicts):
        if ok:
            names.append(key.removesuffix(".dat"))
        else:
            skipped.setdefault(why, []).append(key)

    log.append(f"Scanned {len(keys)} files in parts/, {len(names)} are final bricks")
    for why, ks in sorted(skipped.items()):
        log.append(f"  skipped {len(ks):6d}  {why}")
    return names

def loadExternalIndex(log):
    # {index: brickName} from the binary EXTERNAL_INDEX_FILE, kept exactly as written.
    # Each record's own index field is used, so gaps and any record order are fine.
    path = bpy.path.abspath(EXTERNAL_INDEX_FILE)
    if not os.path.isfile(path):
        raise RuntimeError(f"USE_EXTERNAL_INDEX is on but '{path}' doesn't exist")
    size = os.path.getsize(path)
    if size % INDEX_RECORD.itemsize:
        raise RuntimeError(f"'{path}' is {size} bytes, not a whole number of "
                           f"{INDEX_RECORD.itemsize}-byte records. Is it the fixed-width format?")
    records = np.fromfile(path, dtype=INDEX_RECORD)

    lengths = np.char.str_len(records["name"])  # stored bytes, trailing padding dropped
    bad = np.nonzero(lengths != records["chrAmount"])[0]
    if len(bad):
        raise RuntimeError(f"'{path}': record {bad[0]} has chrAmount {records['chrAmount'][bad[0]]} "
                           f"but {lengths[bad[0]]} name bytes. The file may be corrupt.")
    if len(np.unique(records["index"])) != len(records):
        raise RuntimeError(f"'{path}': the same index appears in more than one record")

    indexMap = {}
    for i, raw in zip(records["index"].tolist(), records["name"].tolist()):
        # Same form the mapper produces: lowercase part number, no .dat
        indexMap[i] = raw.decode("utf-8").strip().replace("\\", "/").lower().removesuffix(".dat")

    indexMap = dict(sorted(indexMap.items()))
    names = list(indexMap.values())
    dupes = len(names) - len(set(names))
    gaps = (max(indexMap) + 1 - len(indexMap)) if indexMap else 0
    log.append(f"Using external index '{path}': {len(indexMap)} bricks")
    if dupes:
        log.append(f"  WARNING, {dupes} duplicate names; each is imported once and shared")
    if gaps:
        log.append(f"  NOTE, {gaps} unused indices between 0 and {max(indexMap)}")
    return indexMap, path

# ---------------------------------------------------------------------------
# 2. Import (threaded parse, main-thread build)
# ---------------------------------------------------------------------------

@contextmanager
def threadSafeImport(prefetched):
    # - Resolves the library roots once on the main thread, so worker threads never
    #   read bpy preferences.
    # - Routes makeBrick's top-level parse to the result a worker already produced.
    #   Recursive subfile calls (which pass positional args) go straight through.
    root, shadow = library.getPartLibrary(), library.getShadowLibrary()
    library.getAllParts()           # make sure the index is built
    library.getSearchDirs()         # warm the lru caches
    library.getShadowSearchDirs()

    origPart, origShadow, origParse = library.getPartLibrary, library.getShadowLibrary, parser.parsePart

    def parsePart(file, *args, **kwargs):
        if not args and kwargs.get("output") is None:
            hit = prefetched.pop(file, None)
            if hit is not None:
                return hit
        return origParse(file, *args, **kwargs)

    library.getPartLibrary = lambda: root
    library.getShadowLibrary = lambda: shadow
    parser.parsePart = parsePart
    try:
        yield origParse
    finally:
        library.getPartLibrary, library.getShadowLibrary = origPart, origShadow
        parser.parsePart = origParse

def getLibraryCollection():
    coll = bpy.data.collections.get(COLLECTION_NAME)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION_NAME)
    scene = bpy.context.scene
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)

    def find(layer):
        if layer.collection == coll:
            return layer
        for child in layer.children:
            hit = find(child)
            if hit:
                return hit
    layer = find(bpy.context.view_layer.layer_collection)
    if layer:
        layer.exclude = True  # not drawn or evaluated on its own; Object Info still reads it
    coll.hide_render = True
    return coll

def findExisting(names, coll):
    # {brickName: object} for bricks already in the collection from an earlier run
    objs = {}
    if REUSE_EXISTING:
        wanted = set(names)
        for o in coll.objects:
            n = o.get("lego_brick")
            if n in wanted and o.type == 'MESH' and len(o.data.polygons):
                objs[n] = o
    return objs

def reportProgress(done, nFailed, total, start):
    elapsed = time.perf_counter() - start
    rate = done / max(elapsed, 1e-6)
    eta = (total - done) / max(rate, 1e-6)
    progress(f"{done}/{total} bricks ({100 * done / max(total, 1):.0f}%), "
             f"{nFailed} failed, {rate:.1f}/s, "
             f"{elapsed / 60:.1f} min elapsed, ~{eta / 60:.1f} min left")

def importTodo(todo, coll, threads, onProgress):
    # Import every name in todo into coll. Returns ({name: object}, [(name, reason)]).
    # onProgress(done, nFailed) is called after each batch of finished bricks.
    objs, failed = {}, []
    prefetched = {}  # partPath -> parsed data, consumed by makeBrick

    with threadSafeImport(prefetched) as origParse, ThreadPoolExecutor(threads) as pool:
        def parseJob(name):
            try:
                path = library.resolvePart(name + ".dat")
                if path is None:
                    return name, None, None, "not found"
                path = str(path)
                return name, path, origParse(path, col=16, matrix=importer.LD_TO_BL), None
            except Exception as e:
                return name, None, None, f"parse error: {e!r}"

        queue = iter(todo)
        pending = set()

        def topUp():
            # Keep a bounded number of parses in flight so memory stays flat
            while len(pending) < threads * 4:
                name = next(queue, None)
                if name is None:
                    return
                pending.add(pool.submit(parseJob, name))

        topUp()
        done = 0
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            topUp()
            for fut in finished:
                name, path, data, err = fut.result()
                done += 1
                if err:
                    failed.append((name, err))
                    continue
                prefetched[path] = data
                try:
                    obj = importer.makeBrick(name)
                except Exception as e:
                    obj, err = None, f"build error: {e!r}"
                prefetched.pop(path, None)  # in case makeBrick didn't consume it

                if obj is None:
                    failed.append((name, err or "makeBrick returned None"))
                    continue
                if len(obj.data.polygons) == 0:
                    mesh = obj.data
                    bpy.data.objects.remove(obj)
                    if mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
                    failed.append((name, "no geometry"))
                    continue

                obj["lego_brick"] = name
                coll.objects.link(obj)
                objs[name] = obj
            onProgress(done, len(failed))
    return objs, failed

def logFailures(failed, log):
    if failed:
        log.append(f"{len(failed)} bricks left out of the index:")
        log.extend(f"  {n}: {why}" for n, why in failed)

def importBricks(names, coll, log):
    # {brickName: object} for every brick that imported with geometry
    objs = findExisting(names, coll)
    todo = [n for n in names if n not in objs]
    if objs:
        log.append(f"Reusing {len(objs)} bricks already in '{COLLECTION_NAME}'")
        progress(f"Reusing {len(objs)} bricks already in '{COLLECTION_NAME}'")
    if not todo:
        return objs
    if PARALLEL_INSTANCES > 1 and len(todo) > 1:
        objs.update(parallelImport(todo, coll, log))
        return objs

    wm = bpy.context.window_manager
    wm.progress_begin(0, len(todo))
    start = time.perf_counter()
    nextReport = [PROGRESS_EVERY]

    def onProgress(done, nFailed):
        wm.progress_update(done)
        if done >= nextReport[0] or done == len(todo):
            nextReport[0] = (done // PROGRESS_EVERY + 1) * PROGRESS_EVERY
            reportProgress(done, nFailed, len(todo), start)

    progress(f"Importing {len(todo)} bricks with {WORKERS} threads...")
    new, failed = importTodo(todo, coll, WORKERS, onProgress)
    objs.update(new)
    wm.progress_end()
    log.append(f"Imported {len(new)} of {len(todo)} bricks "
               f"in {time.perf_counter() - start:.1f}s with {WORKERS} threads")
    logFailures(failed, log)
    return objs

# ---------------------------------------------------------------------------
# 2b. Parallel import across background Blender instances
# ---------------------------------------------------------------------------
#
# Each worker is this same script, run as `blender --background --python <script>
# -- --lego-worker <job.json>`. It imports its share of the bricks with the normal
# in-process path, writes the brick meshes (and what they use) to a temporary .blend,
# and exits. The main file then appends every worker's meshes, makes an object for
# each with importer.getBrickObj in COLLECTION_NAME, swaps the per-worker copies of the shared material, shader
# group and images for one set, and deletes the temporary files.

WORKER_FLAG = "--lego-worker"
PROGRESS_MARK = "@@LEGO_PROGRESS"
RESULT_MARK = "@@LEGO_RESULT"
WORKER_REPORT_EVERY = 25  # bricks between a worker's progress messages to the main file

def scriptSource():
    # A path to this script that a new Blender process can run. A script run from
    # the Text Editor has no file of its own (or one that may be out of date), so
    # the text block's current contents are written out.
    text = bpy.data.texts.get(os.path.basename(__file__))
    if text is None and os.path.isfile(__file__):
        return __file__, None
    if text is None:
        raise RuntimeError("Can't find this script's file or text block to start workers with")
    tmp = os.path.join(tempfile.gettempdir(), f"lego_worker_{os.getpid()}.py")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text.as_string())
    return tmp, tmp

def workerMain(jobPath):
    with open(jobPath, encoding="utf-8") as f:
        job = json.load(f)
    if library.getPartLibrary() is None:
        raise RuntimeError("Worker: no LDraw part library set in the Lego Editor preferences")

    coll = bpy.data.collections.new(COLLECTION_NAME)
    bpy.context.scene.collection.children.link(coll)

    def onProgress(done, nFailed):
        if done % WORKER_REPORT_EVERY == 0 or done == len(job["names"]):
            print(f"{PROGRESS_MARK} {done} {nFailed}", flush=True)

    objs, failed = importTodo(job["names"], coll, WORKER_THREADS, onProgress)
    # Hand back meshes only: appending objects is ~3x slower than appending meshes,
    # and the main file rebuilds each object with importer.getBrickObj anyway.
    # The write pulls in what the meshes use (materials, shader group, images).
    for name, obj in objs.items():
        obj.data["lego_brick"] = name
    bpy.data.libraries.write(job["blend"], {o.data for o in objs.values()},
                             path_remap='ABSOLUTE', compress=False)
    with open(job["result"], "w", encoding="utf-8") as f:
        json.dump({"imported": sorted(objs), "failed": failed}, f)
    print(f"{RESULT_MARK} {len(objs)} {len(failed)}", flush=True)

def relay(proc, k, counts, lock):
    # Pass a worker's console output through, and collect its progress messages
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith(PROGRESS_MARK):
            done, nFailed = map(int, line.split()[1:3])
            with lock:
                counts[k] = (done, nFailed)
        elif line and not line.startswith(RESULT_MARK):
            print(f"  [worker {k}] {line}", flush=True)

def mergeShared(before):
    # Each worker created its own LEGO_MAT material, LEGO_SN shader group and texture
    # images, so the append brings in "LEGO_MAT.001" etc. Point everything at one copy.
    def base(name):
        head, dot, tail = name.rpartition(".")
        return head if dot and tail.isdigit() else name

    def merge(collection, keyOf, ids):
        keep = {}
        for idb in sorted(collection, key=lambda d: (d.name not in before, d.name)):
            key = keyOf(idb)
            if key is None:
                continue
            if key in keep:
                if idb.name not in before:  # only ever merge away what was just appended
                    idb.user_remap(keep[key])
                    collection.remove(idb)
            else:
                keep[key] = idb

    shared = {"LEGO_MAT"}
    merge(bpy.data.node_groups, lambda g: base(g.name) if base(g.name) == importer.SHADER_GROUP else None, ())
    merge(bpy.data.materials, lambda m: base(m.name) if base(m.name) in shared else None, ())
    merge(bpy.data.images, lambda i: bpy.path.abspath(i.filepath) if i.filepath else None, ())

def parallelImport(todo, coll, log):
    # {brickName: object} imported across PARALLEL_INSTANCES background Blenders
    nInst = min(PARALLEL_INSTANCES, len(todo))
    binary = BLENDER_BINARY or bpy.app.binary_path
    if not binary:
        raise RuntimeError("Can't find the Blender executable; set BLENDER_BINARY")
    script, tmpScript = scriptSource()
    tmp = tempfile.mkdtemp(prefix="lego_import_")
    start = time.perf_counter()
    progress(f"Importing {len(todo)} bricks with {nInst} Blender instances "
             f"x {WORKER_THREADS} threads...")

    procs, jobs = [], []
    for k in range(nInst):
        job = {"names": todo[k::nInst],  # interleaved, so big parts spread across workers
               "blend": os.path.join(tmp, f"shard_{k}.blend"),
               "result": os.path.join(tmp, f"shard_{k}.json")}
        jobPath = os.path.join(tmp, f"job_{k}.json")
        with open(jobPath, "w", encoding="utf-8") as f:
            json.dump(job, f)
        cmd = [binary, "--background", "--python-exit-code", "1",
               "--python", script, "--", WORKER_FLAG, jobPath]
        procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8", errors="replace"))
        jobs.append(job)

    counts, lock = {}, threading.Lock()
    relays = [threading.Thread(target=relay, args=(p, k, counts, lock), daemon=True)
              for k, p in enumerate(procs)]
    for r in relays:
        r.start()

    wm = bpy.context.window_manager
    wm.progress_begin(0, len(todo))
    nextReport = PROGRESS_EVERY
    try:
        while any(p.poll() is None for p in procs):
            time.sleep(0.25)
            with lock:
                done = sum(d for d, _ in counts.values())
                nFailed = sum(f for _, f in counts.values())
            wm.progress_update(done)
            if done >= nextReport:
                nextReport = (done // PROGRESS_EVERY + 1) * PROGRESS_EVERY
                reportProgress(done, nFailed, len(todo), start)
        for r in relays:
            r.join()
        wm.progress_end()
        importSecs = time.perf_counter() - start

        # Merge every worker's bricks into the main collection
        progress("Merging worker results...")
        before = {d.name for d in (*bpy.data.materials, *bpy.data.node_groups, *bpy.data.images)}
        objs, failed = {}, []
        for k, (p, job) in enumerate(zip(procs, jobs)):
            if p.returncode != 0 or not os.path.isfile(job["blend"]) or not os.path.isfile(job["result"]):
                failed += [(n, f"worker {k} failed (exit code {p.returncode})") for n in job["names"]]
                continue
            with open(job["result"], encoding="utf-8") as f:
                result = json.load(f)
            failed += [tuple(x) for x in result["failed"]]
            with bpy.data.libraries.load(job["blend"], link=False) as (src, dst):
                dst.meshes = list(src.meshes)
            for mesh in dst.meshes:
                name = mesh.get("lego_brick") if mesh else None
                if name is None:
                    continue
                obj = importer.getBrickObj(name, mesh, 16)  # same object setup as makeBrick
                obj["lego_brick"] = name
                coll.objects.link(obj)
                objs[name] = obj
            missing = set(result["imported"]) - objs.keys()
            failed += [(n, f"missing from worker {k}'s file") for n in sorted(missing)]
        mergeShared(before)
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
        shutil.rmtree(tmp, ignore_errors=True)
        if tmpScript:
            try:
                os.remove(tmpScript)
            except OSError:
                pass

    reportProgress(len(objs) + len(failed), len(failed), len(todo), start)
    log.append(f"Imported {len(objs)} of {len(todo)} bricks in {importSecs:.1f}s with "
               f"{nInst} Blender instances x {WORKER_THREADS} threads, "
               f"merged in {time.perf_counter() - start - importSecs:.1f}s")
    logFailures(failed, log)
    return objs

    prefetched = {}  # partPath -> parsed data, consumed by makeBrick
    failed = []
    wm = bpy.context.window_manager
    wm.progress_begin(0, len(todo))
    start = time.perf_counter()

    with threadSafeImport(prefetched) as origParse, ThreadPoolExecutor(WORKERS) as pool:
        def parseJob(name):
            try:
                path = library.resolvePart(name + ".dat")
                if path is None:
                    return name, None, None, "not found"
                path = str(path)
                return name, path, origParse(path, col=16, matrix=importer.LD_TO_BL), None
            except Exception as e:
                return name, None, None, f"parse error: {e!r}"

        queue = iter(todo)
        pending = set()

        def topUp():
            # Keep a bounded number of parses in flight so memory stays flat
            while len(pending) < WORKERS * 4:
                name = next(queue, None)
                if name is None:
                    return
                pending.add(pool.submit(parseJob, name))

        topUp()
        done = 0
        nextReport = PROGRESS_EVERY
        progress(f"Importing {len(todo)} bricks with {WORKERS} threads...")
        while pending:
            finished, pending = wait(pending, return_when=FIRST_COMPLETED)
            topUp()
            for fut in finished:
                name, path, data, err = fut.result()
                done += 1
                if err:
                    failed.append((name, err))
                    continue
                prefetched[path] = data
                try:
                    obj = importer.makeBrick(name)
                except Exception as e:
                    obj, err = None, f"build error: {e!r}"
                prefetched.pop(path, None)  # in case makeBrick didn't consume it

                if obj is None:
                    failed.append((name, err or "makeBrick returned None"))
                    continue
                if len(obj.data.polygons) == 0:
                    mesh = obj.data
                    bpy.data.objects.remove(obj)
                    if mesh.users == 0:
                        bpy.data.meshes.remove(mesh)
                    failed.append((name, "no geometry"))
                    continue

                obj["lego_brick"] = name
                coll.objects.link(obj)
                objs[name] = obj

            wm.progress_update(done)
            if done >= nextReport or not pending:
                nextReport = (done // PROGRESS_EVERY + 1) * PROGRESS_EVERY
                elapsed = time.perf_counter() - start
                rate = done / max(elapsed, 1e-6)
                eta = (len(todo) - done) / max(rate, 1e-6)
                progress(f"{done}/{len(todo)} bricks ({100 * done / len(todo):.0f}%), "
                         f"{len(failed)} failed, {rate:.1f}/s, "
                         f"{elapsed / 60:.1f} min elapsed, ~{eta / 60:.1f} min left")

    wm.progress_end()
    log.append(f"Imported {len(todo) - len(failed)} of {len(todo)} bricks "
               f"in {time.perf_counter() - start:.1f}s with {WORKERS} threads")
    if failed:
        log.append(f"{len(failed)} bricks left out of the index:")
        log.extend(f"  {n}: {why}" for n, why in failed)
    return objs

# ---------------------------------------------------------------------------
# 3. Index map
# ---------------------------------------------------------------------------

def saveMap(indexMap, log, writeFile=True):
    text = json.dumps({str(i): n for i, n in indexMap.items()}, indent=1)
    block = bpy.data.texts.get(MAP_TEXT_NAME) or bpy.data.texts.new(MAP_TEXT_NAME)
    block.from_string(text)
    if writeFile and MAP_FILE:
        if MAP_FILE.startswith("//") and not bpy.data.filepath:
            log.append(f"Blend file isn't saved, so the map is only in the '{MAP_TEXT_NAME}' text block")
        else:
            path = bpy.path.abspath(MAP_FILE)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            log.append(f"Index map written to {path}")

def writeLog(log):
    block = bpy.data.texts.get(LOG_TEXT_NAME) or bpy.data.texts.new(LOG_TEXT_NAME)
    block.from_string("\n".join(log) + "\n")

# ---------------------------------------------------------------------------

def main():
    if library.getPartLibrary() is None:
        raise RuntimeError("No LDraw part library set in the Lego Editor preferences")
    log = []
    t0 = time.perf_counter()

    if USE_EXTERNAL_INDEX:
        external, extPath = loadExternalIndex(log)
        if LIMIT > 0:
            external = dict(list(external.items())[:LIMIT])
            log.append(f"LIMIT set: using the first {len(external)}")
        names = list(dict.fromkeys(external.values()))  # unique, in index order
    else:
        names = mapFinalBricks(log)
        if LIMIT > 0:
            names = names[:LIMIT]
            log.append(f"LIMIT set: using the first {len(names)}")

    coll = getLibraryCollection()
    objs = importBricks(names, coll, log)

    if USE_EXTERNAL_INDEX:
        # Order is fixed by the file: nothing is renumbered, failures keep their slot
        indexMap = external
        missing = [(i, n) for i, n in indexMap.items() if n not in objs]
        if missing:
            log.append(f"{len(missing)} indices have no brick object:")
            log.extend(f"  {i}: {n}" for i, n in missing)
        saveMap(indexMap, log, writeFile=False)
        log.append(f"External index left untouched: {extPath}")
        count = len(indexMap) - len(missing)
    else:
        ordered = [n for n in names if n in objs]  # map order, failures dropped
        indexMap = dict(enumerate(ordered))
        saveMap(indexMap, log)
        count = len(indexMap)

    log.append(f"Library '{coll.name}': {count} bricks indexed, "
               f"total {time.perf_counter() - t0:.1f}s")
    writeLog(log)
    shown = log if len(log) <= 15 else log[:12] + ["..."] + log[-2:]
    print("\n".join("LEGO LIBRARY: " + l for l in shown))
    print(f"LEGO LIBRARY: full log in the '{LOG_TEXT_NAME}' text block")
    return indexMap

if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if args[:1] == [WORKER_FLAG]:
        workerMain(args[1])   # started by parallelImport in another Blender
    else:
        INDEX_MAP = main()