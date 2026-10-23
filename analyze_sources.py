#!/usr/bin/env python3
"""
Scan every source/header file in the repo and decide which C/C++/ObjC files must
NOT be compiled as standalone translation units in the Xcode project.

Rules (a file matching any rule is listed in the project but not compiled):
  1. *.inc.c / *.inc.cpp ...        -> include fragments (SM64 behaviors, geo, model data)
  2. any source file that another file #includes (e.g. #include "wind.inc.c")
  3. host-tool / doc / script directories (tools/, docs/, ...)
  4. platform-specific file names (dxgi, direct3d, glx, x11, wayland, win32 ...)
  5. unguarded platform-only includes (<windows.h>, <d3d11.h>, <X11/Xlib.h> ... outside #if)

Writes:  --out     exclude list  ("path<TAB>reason" per line, read by gen_xcodeproj.py)
         --report  human-readable report (also includes warnings / things needing a port)
"""
import argparse, fnmatch, os, posixpath, re, sys
from collections import defaultdict

COMPILE = {".c", ".cc", ".cpp", ".cxx", ".m", ".mm"}
HEADERS = {".h", ".hpp", ".hh", ".hxx", ".inc"}
PRUNE = {".git", "build", "DerivedData", "node_modules", ".svn"}
DIR_EXCLUDES = ["tools/*", "docs/*", "doc/*", ".github/*", "scripts/*", "tests/*", "test/*"]
NAME_PATTERNS = ["*dxgi*", "*direct3d*", "*d3d11*", "*d3d12*", "*_glx*", "*wayland*",
                 "*x11*", "*win32*", "*_wgl*"]
PLATFORM_INC = re.compile(
    r"^(windows|windowsx|winsock2|ws2tcpip|d3d\d+\w*|d3dcompiler|dxgi\w*|direct|io|shellapi|"
    r"shlobj|psapi|tlhelp32|synchapi|wrl.*|x11/.*|gl/glx|wayland-.*|alsa/.*|pulse/.*|linux/.*|"
    r"sys/inotify|sys/epoll)(\.h)?$", re.I)
GLEW_INC = re.compile(r"^GL/(glew|gl|glext)\.h$|^GL/glu\.h$")

INC_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
IF_RE = re.compile(r"^\s*#\s*if(n?def)?\b")
END_RE = re.compile(r"^\s*#\s*endif\b")
MAIN_RE = re.compile(r"^\s*(int|void)\s+main\s*\(")

def scan(path):
    includes, unguarded, mains = [], [], 0
    depth, in_c = 0, False
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if in_c:
                if "*/" not in line: continue
                line = line.split("*/", 1)[1]; in_c = False
            while "/*" in line:
                pre, _, post = line.partition("/*")
                if "*/" in post: line = pre + post.split("*/", 1)[1]
                else: line, in_c = pre, True; break
            if line.lstrip().startswith("//"): continue
            if IF_RE.match(line): depth += 1; continue
            if END_RE.match(line): depth = max(0, depth - 1); continue
            m = INC_RE.match(line)
            if m:
                includes.append(m.group(1).replace("\\", "/"))
                if depth == 0: unguarded.append(includes[-1])
            elif MAIN_RE.match(line): mains += 1
    return includes, unguarded, mains

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="build/exclude-sources.txt")
    ap.add_argument("--report", default="build/source-analysis.txt")
    ap.add_argument("--extra-exclude", action="append", default=[], help="extra glob to not compile")
    ap.add_argument("--keep", action="append", default=[], help="glob that must always be compiled")
    a = ap.parse_args()
    root = os.path.abspath(a.root)

    files = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in PRUNE and not d.endswith(".xcodeproj"))
        for f in sorted(fn):
            files.append(os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/"))
    fset = set(files)
    by_base = defaultdict(list)
    for p in files: by_base[posixpath.basename(p)].append(p)

    reasons = {}
    def mark(p, why): reasons.setdefault(p, why)

    included_by = defaultdict(set)
    plat_hits, glew_hits, mains = {}, [], {}
    scanned = 0
    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in COMPILE and ext not in HEADERS: continue
        full = os.path.join(root, rel)
        if os.path.islink(full) and not os.path.exists(full): continue
        if os.path.getsize(full) > 64 * 1024 * 1024: continue
        scanned += 1
        incs, unguarded, nmain = scan(full)
        if nmain: mains[rel] = nmain
        d = posixpath.dirname(rel)
        for inc in incs:
            cands = [posixpath.normpath(posixpath.join(d, inc)), posixpath.normpath(inc),
                     "src/" + inc, "include/" + inc]
            target = next((c for c in cands if c in fset), None)
            if target is None and "/" in inc:
                target = next((p for p in by_base.get(posixpath.basename(inc), [])
                               if p.endswith("/" + inc)), None)
            if target and os.path.splitext(target)[1].lower() in COMPILE and target != rel:
                included_by[target].add(rel)
        if ext in COMPILE:
            bad = [i for i in unguarded if PLATFORM_INC.match(i)]
            if bad: plat_hits[rel] = bad
            g = [i for i in unguarded if GLEW_INC.match(i)]
            if g: glew_hits.append(rel)

    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        if ext not in COMPILE: continue
        base = posixpath.basename(rel)
        if re.search(r"\.inc\.[^.]+$", base): mark(rel, "include fragment (*.inc.c)")
        if rel in included_by:
            inc_by = sorted(included_by[rel])[0]
            mark(rel, f"#included by {inc_by}")
        for g in DIR_EXCLUDES + a.extra_exclude:
            if fnmatch.fnmatch(rel, g): mark(rel, f"excluded directory/glob {g}"); break
        if any(fnmatch.fnmatch(base.lower(), g) for g in NAME_PATTERNS):
            mark(rel, "platform-specific file name")
        if rel in plat_hits: mark(rel, "unguarded platform include <" + plat_hits[rel][0] + ">")
    for rel in list(reasons):
        if any(fnmatch.fnmatch(rel, g) for g in a.keep): del reasons[rel]

    compiled = [p for p in files if os.path.splitext(p)[1].lower() in COMPILE and p not in reasons]
    dup = defaultdict(list)
    for p in compiled: dup[posixpath.basename(p)].append(p)
    dup = {k: v for k, v in dup.items() if len(v) > 1}

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        for p in sorted(reasons): f.write(f"{p}\t{reasons[p]}\n")

    cats = defaultdict(int)
    for w in reasons.values(): cats[w.split(" ")[0] + " " + (w.split(" ")[1] if " " in w else "")] += 1
    lines = [f"Scanned {scanned} source/header files out of {len(files)} total files",
             f"Compile-type files: {len(compiled) + len(reasons)}  ->  compiled: {len(compiled)}, "
             f"listed only: {len(reasons)}", "", "Excluded by reason:"]
    lines += [f"  {n:6d}  {c.strip()}" for c, n in sorted(cats.items(), key=lambda x: -x[1])]
    lines += ["", "== Excluded files =="] + [f"  {p}   [{reasons[p]}]" for p in sorted(reasons)]
    if glew_hits:
        lines += ["", "== WARNING: desktop OpenGL/GLEW headers (<GL/...>) used unguarded =="
                  "  (not available on iOS - needs a GLES port or shim header)"]
        lines += [f"  {p}" for p in sorted(glew_hits)]
    if len(mains) > 1:
        lines += ["", "== WARNING: several files define main() - link will fail =="]
        lines += [f"  {p}" for p in sorted(mains)]
    if dup:
        lines += ["", "== NOTE: compiled files sharing a base name (possible object collisions) =="]
        lines += [f"  {k}: {', '.join(v)}" for k, v in sorted(dup.items())]
    with open(a.report, "w") as f: f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:12]))
    print(f"-> {a.out} ({len(reasons)} entries), {a.report}")

if __name__ == "__main__":
    sys.exit(main())
