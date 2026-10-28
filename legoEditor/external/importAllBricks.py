"""
Build a Geometry Nodes group that holds every final LDraw brick as an instance.

Run from Blender's Text Editor (or `blender file.blend --python buildBrickInstances.py`)
with the Lego Editor addon enabled and its part library set in the addon preferences.

What it does
  1. Map:    scans the top level of parts/ and keeps the files that are final models
             (Part / Shortcut), dropping subparts, primitives, "~" helper parts,
             "~Moved to" stubs, aliases and physical-colour variants (configurable below).
  2. Import: parses the bricks on a thread pool with the addon's own parser, then
             builds each one on the main thread with importer.makeBrick (bpy data
             can only be created on the main thread). Bricks land in a collection
             that is excluded from the view layer.
  3. Build:  creates NODE_GROUP_NAME with one Object Info node per brick, linked into
             Geometry to Instance in index order, so instance i is brick i.
             Building one tree with tens of thousands of nodes from Python takes hours,
             so a full library is split into sub-groups of CHUNK_SIZE bricks (each one
             Object Info -> Geometry to Instance) that the main group joins in order.
             The output is still one flat instance list: instance i is brick i.
  4. Save:   writes the {index: brickName} map to a text block in the .blend and,
             if MAP_FILE is set, to a JSON file.

Only bricks that imported with geometry get an index, so the map always matches the
node group exactly.
"""

import bpy
import os
import sys
import json
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

NODE_GROUP_NAME = "LEGO_BrickInstances"
COLLECTION_NAME = "LEGO_BrickLibrary"
MAP_TEXT_NAME = "lego_brick_index.json"
LOG_TEXT_NAME = "lego_brick_import_log.txt"
MAP_FILE = "//lego_brick_index.json"  # "//" = next to the .blend; "" to skip writing a file

INCLUDE_SHORTCUTS = True          # pre-assembled parts (e.g. hinge assemblies, minifig legs)
INCLUDE_ALIASES = False           # "=" parts that just reference another part number
INCLUDE_PHYSICAL_COLOUR = False   # "_" parts that are another part in a fixed colour
INCLUDE_FLEXIBLE_SECTIONS = False # hose / cable segments

LIMIT = 0              # > 0 imports only the first N mapped bricks (handy for a test run)
WORKERS = max(1, os.cpu_count() or 4)
REUSE_EXISTING = True  # keep bricks already in COLLECTION_NAME instead of re-importing them
CHUNK_SIZE = 256       # bricks per sub-group once the library is bigger than this (see buildNodeGroup)

COLUMN_ROWS = 64       # Object Info nodes per column in the node editor
NODE_DX, NODE_DY = 220, 190

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

def importBricks(names, coll, log):
    # {brickName: object} for every brick that imported with geometry
    objs = {}
    if REUSE_EXISTING:
        for o in coll.objects:
            n = o.get("lego_brick")
            if n in names and o.type == 'MESH' and len(o.data.polygons):
                objs[n] = o
    todo = [n for n in names if n not in objs]
    if objs:
        log.append(f"Reusing {len(objs)} bricks already in '{COLLECTION_NAME}'")
    if not todo:
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
            if done % 250 < len(finished) or not pending:
                rate = done / max(time.perf_counter() - start, 1e-6)
                eta = (len(todo) - done) / max(rate, 1e-6)
                print(f"LEGO INSTANCES: {done}/{len(todo)} bricks  ({rate:.1f}/s, ~{eta/60:.1f} min left)")

    wm.progress_end()
    log.append(f"Imported {len(todo) - len(failed)} of {len(todo)} bricks "
               f"in {time.perf_counter() - start:.1f}s with {WORKERS} threads")
    if failed:
        log.append(f"{len(failed)} bricks left out of the index:")
        log.extend(f"  {n}: {why}" for n, why in failed)
    return objs

# ---------------------------------------------------------------------------
# 3. Node group
# ---------------------------------------------------------------------------

def linkInOrder(tree, outputs, socket):
    # Blender puts the newest link at the top of a multi-input socket and evaluates
    # top to bottom, so link in reverse to get outputs[0] first. Checked afterwards.
    for out in reversed(outputs):
        tree.links.new(out, socket)
    got = [l.from_socket for l in socket.links]
    if got != list(outputs):
        raise RuntimeError(f"Link order on '{socket.node.name}' did not come out as expected "
                           f"({len(got)} links, expected {len(outputs)})")

def freshGroup(name):
    # Empty geometry node group with a single "Instances" output
    group = bpy.data.node_groups.get(name)
    if group is None:
        group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    group.nodes.clear()
    group.interface.clear()
    group.interface.new_socket(name="Instances", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    return group

def fillBrickGroup(group, chunk, start):
    # Object Info per brick -> one Geometry to Instance -> output, in order
    nodes = group.nodes
    infoOuts = []
    for j, (name, obj) in enumerate(chunk):
        i = start + j
        n = nodes.new("GeometryNodeObjectInfo")
        n.label = f"{i}: {name}"
        n.inputs["Object"].default_value = obj
        n.location = ((j // COLUMN_ROWS) * NODE_DX, -(j % COLUMN_ROWS) * NODE_DY)
        n.hide = True
        infoOuts.append(n.outputs["Geometry"])

    x = max(1, -(-len(chunk) // COLUMN_ROWS)) * NODE_DX + 200
    gti = nodes.new("GeometryNodeGeometryToInstance")
    gti.label = f"Bricks {start}-{start + len(chunk) - 1}"
    gti.location = (x, 0)
    if infoOuts:
        linkInOrder(group, infoOuts, gti.inputs["Geometry"])
    out = nodes.new("NodeGroupOutput")
    out.location = (x + 250, 0)
    group.links.new(gti.outputs["Instances"], out.inputs[0])

def removeOldChunks():
    prefix = NODE_GROUP_NAME + "_"
    for g in [g for g in bpy.data.node_groups if g.name.startswith(prefix) and g.get("lego_brick_chunk")]:
        bpy.data.node_groups.remove(g)

def buildNodeGroup(ordered):
    # ordered: [(brickName, object)] in index order. The output is a flat instance
    # list where instance i is brick i.
    #
    # Every node/link added through Python costs time proportional to the size of
    # the tree it lands in, so one 20k-node tree takes hours to build (and is
    # sluggish to open). Up to CHUNK_SIZE bricks go straight into the main group;
    # beyond that, each CHUNK_SIZE bricks get their own sub-group (same Object Info
    # -> Geometry to Instance layout) and the main group joins them in order.
    # Join Geometry appends instances in input order, so indices stay flat.
    removeOldChunks()
    group = freshGroup(NODE_GROUP_NAME)
    size = min(CHUNK_SIZE, 4095)

    if len(ordered) <= size:
        fillBrickGroup(group, ordered, 0)
    else:
        nodes = group.nodes
        chunkOuts = []
        for c, s in enumerate(range(0, len(ordered), size)):
            sub = freshGroup(f"{NODE_GROUP_NAME}_{c:03d}")
            sub["lego_brick_chunk"] = c
            fillBrickGroup(sub, ordered[s:s + size], s)
            gn = nodes.new("GeometryNodeGroup")
            gn.node_tree = sub
            gn.label = f"Bricks {s}-{min(s + size, len(ordered)) - 1}"
            gn.location = (0, -c * 60)
            gn.hide = True
            chunkOuts.append(gn.outputs[0])
            if c % 10 == 0:
                print(f"LEGO INSTANCES: node groups {s}/{len(ordered)} bricks")

        join = nodes.new("GeometryNodeJoinGeometry")
        join.location = (400, 0)
        linkInOrder(group, chunkOuts, join.inputs["Geometry"])
        out = nodes.new("NodeGroupOutput")
        out.location = (650, 0)
        group.links.new(join.outputs["Geometry"], out.inputs[0])

    group["brick_count"] = len(ordered)
    return group

# ---------------------------------------------------------------------------
# 4. Index map
# ---------------------------------------------------------------------------

def saveMap(indexMap, log):
    text = json.dumps({str(i): n for i, n in indexMap.items()}, indent=1)
    block = bpy.data.texts.get(MAP_TEXT_NAME) or bpy.data.texts.new(MAP_TEXT_NAME)
    block.from_string(text)
    if MAP_FILE:
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

    names = mapFinalBricks(log)
    if LIMIT > 0:
        names = names[:LIMIT]
        log.append(f"LIMIT set: using the first {len(names)}")

    coll = getLibraryCollection()
    objs = importBricks(names, coll, log)

    ordered = [(n, objs[n]) for n in names if n in objs]  # map order, failures dropped
    indexMap = {i: n for i, (n, _) in enumerate(ordered)}
    group = buildNodeGroup(ordered)
    saveMap(indexMap, log)

    log.append(f"Node group '{group.name}': {len(ordered)} bricks, "
               f"total {time.perf_counter() - t0:.1f}s")
    writeLog(log)
    shown = log if len(log) <= 15 else log[:12] + ["..."] + log[-2:]
    print("\n".join("LEGO INSTANCES: " + l for l in shown))
    print(f"LEGO INSTANCES: full log in the '{LOG_TEXT_NAME}' text block")
    return indexMap

if __name__ == "__main__":
    INDEX_MAP = main()