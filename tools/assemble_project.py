#!/usr/bin/env python3
"""Rebuild the real folder layout from a FLAT directory of files.
A claude.ai Project cannot hold folders, so every file is uploaded flat and this script puts each one back
where it belongs, then checks that nothing is missing, stale or from an old version.

    python assemble_project.py [flat_dir] [dest_dir]          default: /mnt/project  ->  /tmp/aegis_build

Layout rule (names are unique, so the file name alone decides the folder):
    app.py, requirements.txt, README.md, LICENSES.md, STATUS.md, *.spec, *.command   -> project root
    test_*.py                                                                         -> tests/
    ocr_check.py, render_check.py, make_sample_scan.py, assemble_project.py           -> tools/
    every other .py                                                                   -> aegis/   (+ empty __init__.py)
"""
import os, re, shutil, sys

ROOT = {"app.py", "requirements.txt", "README.md", "LICENSES.md", "STATUS.md",
        "Aegis_Privacy_Airlock.spec", "build_app.command", "launch.command"}
TOOLS = {"ocr_check.py", "render_check.py", "make_sample_scan.py", "assemble_project.py"}
MODULES = ["batch", "diagnostics", "documents", "folders_ui", "inspector", "mailbox", "ocr", "pdfrender", "providers",
           "redaction", "session", "stores", "theme", "tips", "ui_dialogs", "widgets", "workers"]
TESTS = ["test_core", "test_v12", "test_v13", "test_robustness", "test_ui_flows"]
# (file, text that must be present, text that must be absent, meaning)
SIGNATURES = [
    ("app.py", "APP_VERSION", "providers.LLMClient", "app.py is the OLD v0.9 file"),
    ("ui_dialogs.py", "class SettingsDialog", "class MailboxDialog", "ui_dialogs.py is the OLD v0.9 file"),
    ("documents.py", "def load_document", "default_lang", "documents.py is an old version"),
    ("redaction.py", "class Analysis", "get_analyzer", "redaction.py is an old version"),
]


def destination(name):
    if name in ROOT:
        return name
    if re.fullmatch(r"test_\w+\.py", name):
        return "tests/" + name
    if name in TOOLS:
        return "tools/" + name
    if name.endswith(".py"):
        return "aegis/" + name
    return None


def main(flat="/mnt/project", dest="/tmp/aegis_build"):
    names = sorted(os.listdir(flat))
    shutil.rmtree(dest, ignore_errors=True)
    problems, ignored = [], []
    for n in names:
        d = destination(n)
        if d is None:
            ignored.append(n)
            continue
        os.makedirs(os.path.dirname(os.path.join(dest, d)) or dest, exist_ok=True)
        shutil.copy(os.path.join(flat, n), os.path.join(dest, d))
    os.makedirs(os.path.join(dest, "aegis"), exist_ok=True)
    open(os.path.join(dest, "aegis", "__init__.py"), "w").close()

    for name in ["app.py"] + [m + ".py" for m in MODULES] + [t + ".py" for t in TESTS] + ["requirements.txt"]:
        if name not in names:
            problems.append(f"MISSING  {name}")
    for fname, must, mustnot, meaning in SIGNATURES:
        if fname in names:
            text = open(os.path.join(flat, fname), encoding="utf-8", errors="replace").read()
            if must not in text or mustnot in text:
                problems.append(f"STALE    {meaning}")
    ver = {}
    if "app.py" in names:
        m = re.search(r'APP_VERSION = "([\d.]+)"', open(os.path.join(flat, "app.py")).read())
        ver["app.py"] = m.group(1) if m else "?"
    if "Aegis_Privacy_Airlock.spec" in names:
        m = re.search(r'version="([\d.]+)"', open(os.path.join(flat, "Aegis_Privacy_Airlock.spec")).read())
        ver["spec"] = m.group(1) if m else "?"
    if len(set(ver.values())) > 1:
        problems.append(f"VERSION  files disagree: {ver}")
    print(f"assembled {len(names) - len(ignored)} files into {dest}  (versions: {ver or 'unknown'})")
    for n in ignored:
        print(f"IGNORED  {n}  (not part of the app: delete it from the Project if it is an old output)")
    for p in problems:
        print(p)
    print("RESULT:", "OK" if not problems else "PROBLEMS FOUND")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
