#!/usr/bin/env python3
"""
Scan every file in a repo and generate <name>.xcodeproj/project.pbxproj.

 - Every file appears in the Xcode navigator (folder tree mirrors the repo).
 - C/C++/ObjC sources go in the "Compile Sources" phase of a native target.
 - A second "make" target (PBXLegacyTarget) runs the repo's Makefile, which is
   the reliable way to actually build projects that need generated assets.

Usage: python3 scripts/gen_xcodeproj.py --name Saturn [--exclude 'glob' ...]
"""
import argparse, fnmatch, hashlib, os, re, sys

SRC = {".c": "sourcecode.c.c", ".cc": "sourcecode.cpp.cpp", ".cpp": "sourcecode.cpp.cpp",
       ".cxx": "sourcecode.cpp.cpp", ".m": "sourcecode.c.objc", ".mm": "sourcecode.cpp.objcpp"}
HDR = {".h": "sourcecode.c.h", ".hpp": "sourcecode.cpp.h", ".hh": "sourcecode.cpp.h",
       ".hxx": "sourcecode.cpp.h", ".inc": "sourcecode.c.h"}
OTHER = {".png": "image.png", ".jpg": "image.jpeg", ".jpeg": "image.jpeg", ".json": "text.json",
         ".py": "text.script.python", ".sh": "text.script.sh", ".plist": "text.plist.xml",
         ".xml": "text.xml", ".yml": "text", ".yaml": "text", ".md": "net.daringfireball.markdown",
         ".txt": "text", ".mk": "text", ".s": "sourcecode.asm", ".glsl": "text", ".lua": "text"}
PRUNE_DIRS = {".git", "build", "DerivedData", "node_modules", ".svn"}

def uid(s): return hashlib.md5(s.encode()).hexdigest()[:24].upper()

def q(s):
    if s and re.fullmatch(r"[A-Za-z0-9_./]+", s): return s
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

def fmt(v):
    if isinstance(v, bool): return "1" if v else "0"
    if isinstance(v, int): return str(v)
    if isinstance(v, str): return q(v)
    if isinstance(v, list): return "(" + " ".join(fmt(x) + "," for x in v) + ")"
    if isinstance(v, dict): return "{" + " ".join(f"{k} = {fmt(x)};" for k, x in v.items()) + "}"
    raise TypeError(v)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--name", default="App")
    ap.add_argument("--exclude", action="append", default=[], help="glob of paths to skip entirely")
    ap.add_argument("--exclude-sources", action="append", default=[],
                    help="glob of source files to list but NOT compile")
    ap.add_argument("--define", action="append", default=[], help="preprocessor define, e.g. TARGET_MACOS")
    ap.add_argument("--jobs", default="8")
    a = ap.parse_args()

    root = os.path.abspath(a.root)
    proj_dir = f"{a.name}.xcodeproj"
    files = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in PRUNE_DIRS and not d.endswith(".xcodeproj"))
        for f in sorted(fn):
            if f == ".DS_Store": continue
            rel = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
            if any(fnmatch.fnmatch(rel, g) for g in a.exclude): continue
            if os.path.islink(os.path.join(dp, f)) and not os.path.exists(os.path.join(dp, f)): continue
            files.append(rel)

    # ---- group tree ----
    subdirs, dirfiles = {"": set()}, {"": []}
    for rel in files:
        d = os.path.dirname(rel)
        parts = d.split("/") if d else []
        for i in range(len(parts)):
            parent, cur = "/".join(parts[:i]), "/".join(parts[:i + 1])
            subdirs.setdefault(parent, set()).add(cur)
            subdirs.setdefault(cur, set()); dirfiles.setdefault(cur, [])
        dirfiles.setdefault(d, []).append(rel)

    S = {k: [] for k in ["PBXBuildFile", "PBXFileReference", "PBXGroup", "PBXLegacyTarget",
                         "PBXNativeTarget", "PBXProject", "PBXSourcesBuildPhase",
                         "XCBuildConfiguration", "XCConfigurationList"]}
    def add(sec, id_, **kw): S[sec].append(f"\t\t{id_} = {fmt(kw)};")

    build_ids, header_dirs = [], set()
    def fid(rel): return uid("F:" + rel)

    for rel in files:
        ext = os.path.splitext(rel)[1].lower()
        base = os.path.basename(rel)
        if base in ("Makefile", "makefile"): ftype = "sourcecode.make"
        else: ftype = SRC.get(ext) or HDR.get(ext) or OTHER.get(ext) or "text"
        add("PBXFileReference", fid(rel), isa="PBXFileReference", lastKnownFileType=ftype,
            name=base, path=base, sourceTree="<group>")
        if ext in HDR: header_dirs.add(os.path.dirname(rel))
        if ext in SRC and not any(fnmatch.fnmatch(rel, g) for g in a.exclude_sources):
            bid = uid("B:" + rel)
            add("PBXBuildFile", bid, isa="PBXBuildFile", fileRef=fid(rel))
            build_ids.append(bid)

    prod_ref, prod_grp = uid("PRODREF"), uid("PRODGRP")
    add("PBXFileReference", prod_ref, isa="PBXFileReference", explicitFileType="compiled.mach-o.executable",
        includeInIndex=0, path=a.name, sourceTree="BUILT_PRODUCTS_DIR")
    add("PBXGroup", prod_grp, isa="PBXGroup", children=[prod_ref], name="Products", sourceTree="<group>")

    def gid(d): return uid("G:" + d)
    for d in subdirs:
        kids = [gid(s) for s in sorted(subdirs[d])] + [fid(f) for f in sorted(dirfiles.get(d, []))]
        if d == "":
            add("PBXGroup", gid(""), isa="PBXGroup", children=kids + [prod_grp], sourceTree="<group>")
        else:
            b = os.path.basename(d)
            add("PBXGroup", gid(d), isa="PBXGroup", children=kids, name=b, path=b, sourceTree="<group>")

    # ---- settings ----
    inc = ["$(SRCROOT)", "/opt/homebrew/include", "/usr/local/include"] + \
          ["$(SRCROOT)/" + d for d in sorted(header_dirs) if d]
    common = {"ALWAYS_SEARCH_USER_PATHS": "NO", "SDKROOT": "macosx", "MACOSX_DEPLOYMENT_TARGET": "11.0",
              "CLANG_CXX_LANGUAGE_STANDARD": "gnu++17", "CLANG_CXX_LIBRARY": "libc++",
              "GCC_C_LANGUAGE_STANDARD": "gnu11", "GCC_WARN_INHIBIT_ALL_WARNINGS": "YES",
              "CODE_SIGNING_ALLOWED": "NO", "ENABLE_USER_SCRIPT_SANDBOXING": "NO"}
    native = {**common, "PRODUCT_NAME": a.name, "HEADER_SEARCH_PATHS": inc,
              "LIBRARY_SEARCH_PATHS": ["/opt/homebrew/lib", "/usr/local/lib"],
              "OTHER_LDFLAGS": ["-lSDL2", "-lGLEW", "-lcurl", "-ljsoncpp", "-framework", "OpenGL"]}
    if a.define: native["GCC_PREPROCESSOR_DEFINITIONS"] = a.define + ["$(inherited)"]

    def configs(tag, settings):
        ids = []
        for n in ("Debug", "Release"):
            i = uid(f"C:{tag}:{n}"); ids.append(i)
            add("XCBuildConfiguration", i, isa="XCBuildConfiguration", buildSettings=settings, name=n)
        l = uid(f"L:{tag}")
        add("XCConfigurationList", l, isa="XCConfigurationList", buildConfigurations=ids,
            defaultConfigurationIsVisible=0, defaultConfigurationName="Release")
        return l

    proj_cfg = configs("project", {"ALWAYS_SEARCH_USER_PATHS": "NO", "SDKROOT": "macosx"})
    nat_cfg = configs("native", native)
    leg_cfg = configs("legacy", {"PRODUCT_NAME": f"{a.name} (make)"})

    phase, nat, leg = uid("PHASE"), uid("NATIVE"), uid("LEGACY")
    add("PBXSourcesBuildPhase", phase, isa="PBXSourcesBuildPhase", buildActionMask=2147483647,
        files=build_ids, runOnlyForDeploymentPostprocessing=0)
    add("PBXNativeTarget", nat, isa="PBXNativeTarget", buildConfigurationList=nat_cfg,
        buildPhases=[phase], buildRules=[], dependencies=[], name=a.name, productName=a.name,
        productReference=prod_ref, productType="com.apple.product-type.tool")
    add("PBXLegacyTarget", leg, isa="PBXLegacyTarget", buildArgumentsString=f"-j{a.jobs} $(ACTION)",
        buildConfigurationList=leg_cfg, buildPhases=[], buildToolPath="/usr/bin/make",
        buildWorkingDirectory="", dependencies=[], name=f"{a.name} (make)",
        passBuildSettingsInEnvironment=0, productName=f"{a.name} (make)")
    pid = uid("PROJECT")
    add("PBXProject", pid, isa="PBXProject", attributes={"LastUpgradeCheck": 1500},
        buildConfigurationList=proj_cfg, compatibilityVersion="Xcode 14.0", developmentRegion="en",
        hasScannedForEncodings=0, knownRegions=["en", "Base"], mainGroup=gid(""),
        productRefGroup=prod_grp, projectDirPath="", projectRoot="", targets=[nat, leg])

    out = ["// !$*UTF8*$!", "{", "\tarchiveVersion = 1;", "\tclasses = {", "\t};",
           "\tobjectVersion = 56;", "\tobjects = {", ""]
    for sec, lines in S.items():
        if not lines: continue
        out += [f"/* Begin {sec} section */"] + sorted(lines) + [f"/* End {sec} section */", ""]
    out += ["\t};", f"\trootObject = {pid};", "}", ""]

    os.makedirs(os.path.join(root, proj_dir), exist_ok=True)
    with open(os.path.join(root, proj_dir, "project.pbxproj"), "w") as f:
        f.write("\n".join(out))
    print(f"{proj_dir}: {len(files)} files, {len(build_ids)} compiled, "
          f"{len(header_dirs)} header dirs, {len(out)} pbxproj lines")

if __name__ == "__main__":
    sys.exit(main())
