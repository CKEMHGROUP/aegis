"""
Folder batches: scan, detect new/changed files, sanitise to a shareable folder.

Design (decided with the user): a shared copy is ONE-WAY.
  * The folder you share contains only Document-001.txt, Document-002.txt ... with a header.
    No original file names, no manifest, no mapping.
  * Everything that links outputs back to originals (manifest, report) stays on this Mac.
  * Optional: an AES-GCM encrypted mapping stays on this Mac so the owner can restore real values
    in a returned file. The key is held in the Keychain, never next to the data.
  * Within one batch the same value gets the same placeholder in every file, so a colleague can
    follow "who is who". Different batches are independent.
  * Safe default: items the detector is unsure about are HIDDEN, not left in.
No Qt imports: this module is unit-tested headless.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime

from aegis import documents, redaction

STATUS_NEW, STATUS_MODIFIED, STATUS_UNCHANGED = "new", "modified", "unchanged"
STATUS_MISSING, STATUS_RENAMED = "missing", "renamed"
CHANGE_LABELS = {STATUS_NEW: "New", STATUS_MODIFIED: "Changed", STATUS_UNCHANGED: "Unchanged",
                 STATUS_MISSING: "Removed", STATUS_RENAMED: "Renamed"}

HEADER = (
    "SANITISED COPY · one-way · Aegis {version} · {date} · batch {batch}\n"
    "Placeholders such as <PERSON_1> stand for hidden values. The same value has the same placeholder in every\n"
    "file of this batch. The real values are not in this folder and cannot be recovered from it.\n"
    "------------------------------------------------------------------------\n\n"
)


def base_dir() -> str:
    """Where local-only data lives (manifests, reports, encrypted keys). Never inside a shared folder."""
    home = os.path.expanduser("~")
    mac = os.path.join(home, "Library", "Application Support", "AegisPrivacyAirlock")
    return mac if os.path.isdir(os.path.join(home, "Library")) else os.path.join(home, ".local", "share", "AegisPrivacyAirlock")


# ----------------------------------------------------------------------------- scanning
@dataclass
class FileState:
    rel: str
    path: str
    size: int
    mtime_ns: int
    sha256: str
    status: str
    prev_rel: str = ""          # for "renamed"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


class Manifest:
    """What was processed before, per folder. Kept locally; it contains file names, so it is private."""

    def __init__(self, folder: str, root: str | None = None):
        self.folder = os.path.abspath(folder)
        root = root or os.path.join(base_dir(), "manifests")
        os.makedirs(root, mode=0o700, exist_ok=True)
        key = hashlib.sha1(self.folder.encode("utf-8")).hexdigest()[:16]
        self.path = os.path.join(root, f"{key}.json")
        self.files: dict = {}
        self.batches: list = []
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.files, self.batches = data.get("files", {}), data.get("batches", [])
            except (OSError, ValueError):
                pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"folder": self.folder, "files": self.files, "batches": self.batches}, f, indent=1)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)


def _walk(folder: str, recursive: bool):
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")) if recursive else []
        for name in sorted(filenames):
            if name.startswith(".") or os.path.splitext(name)[1].lower() not in documents.SUPPORTED_EXT:
                continue
            yield os.path.join(dirpath, name)


def scan_folder(folder: str, manifest: Manifest, recursive: bool = True) -> list:
    """Compare the folder with the manifest. Hashes only files whose size or time changed."""
    present, new_candidates = {}, []
    for path in _walk(folder, recursive):
        rel = os.path.relpath(path, folder)
        st = os.stat(path)
        old = manifest.files.get(rel)
        if old and old.get("size") == st.st_size and old.get("mtime_ns") == st.st_mtime_ns:
            present[rel] = FileState(rel, path, st.st_size, st.st_mtime_ns, old["sha256"], STATUS_UNCHANGED)
            continue
        digest = sha256_file(path)
        if old:
            status = STATUS_UNCHANGED if old.get("sha256") == digest else STATUS_MODIFIED
            present[rel] = FileState(rel, path, st.st_size, st.st_mtime_ns, digest, status)
        else:
            fs = FileState(rel, path, st.st_size, st.st_mtime_ns, digest, STATUS_NEW)
            present[rel] = fs
            new_candidates.append(fs)

    states = list(present.values())
    missing = {rel: m for rel, m in manifest.files.items() if rel not in present}
    by_hash = {m["sha256"]: rel for rel, m in missing.items()}
    for fs in new_candidates:                      # same content under a new name = renamed, not new
        prev = by_hash.get(fs.sha256)
        if prev:
            fs.status, fs.prev_rel = STATUS_RENAMED, prev
            missing.pop(prev, None)
    for rel, m in missing.items():
        states.append(FileState(rel, os.path.join(folder, rel), m.get("size", 0), m.get("mtime_ns", 0),
                                m["sha256"], STATUS_MISSING))
    order = {STATUS_NEW: 0, STATUS_MODIFIED: 1, STATUS_RENAMED: 2, STATUS_UNCHANGED: 3, STATUS_MISSING: 4}
    return sorted(states, key=lambda s: (order[s.status], s.rel.lower()))


def pending(states) -> list:
    return [s for s in states if s.status in (STATUS_NEW, STATUS_MODIFIED)]


# ----------------------------------------------------------------------------- encryption (optional key)
def encrypt_mapping(mapping: dict, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = secrets.token_bytes(12)
    return nonce + AESGCM(key).encrypt(nonce, json.dumps(mapping, ensure_ascii=False).encode("utf-8"), b"aegis-mapping-v1")


def decrypt_mapping(blob: bytes, key: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], b"aegis-mapping-v1").decode("utf-8"))


def restore_text(text: str, mapping: dict) -> str:
    return redaction.rehydrate(text, mapping)[0]


# ----------------------------------------------------------------------------- the batch
@dataclass
class FileOutcome:
    rel: str
    status: str = "ok"            # ok | failed
    output: str = ""              # e.g. Document-003.txt
    hidden: int = 0
    uncertain_hidden: int = 0
    ocr_pages: int = 0
    error: str = ""


@dataclass
class BatchResult:
    batch_id: str
    out_dir: str
    outcomes: list = field(default_factory=list)
    key_kept: bool = False

    @property
    def total_hidden(self): return sum(o.hidden for o in self.outcomes)
    @property
    def total_uncertain(self): return sum(o.uncertain_hidden for o in self.outcomes)
    @property
    def ok_count(self): return sum(1 for o in self.outcomes if o.status == "ok")


def out_dir_for(out_root: str, batch_id: str) -> str:
    return os.path.join(out_root, f"Sanitised {batch_id}")


def new_batch_id() -> str:
    return datetime.now().strftime("B-%Y%m%d-%H%M%S")


def sanitize_batch(engine, folder, states, out_root, manifest, *, region=redaction.AUTO_REGION, presets=(),
                   disabled=None, ocr=None, ocr_languages=("en",), keep_key=False, key_store=None,
                   progress=None, app_version="0", batch_id=None, local_root=None, cancel=None) -> BatchResult:
    """Sanitise the given FileStates into <out_root>/Sanitised <batch id>/ ."""
    batch_id = batch_id or new_batch_id()
    out_dir = out_dir_for(out_root, batch_id)
    os.makedirs(out_dir, exist_ok=True)
    local_dir = os.path.join(local_root or os.path.join(base_dir(), "batches"), batch_id)
    os.makedirs(local_dir, mode=0o700, exist_ok=True)

    shared = redaction.new_state()
    result = BatchResult(batch_id, out_dir)
    header = HEADER.format(version=app_version, date=datetime.now().strftime("%Y-%m-%d"), batch=batch_id)
    n_files = len(states)
    for i, st in enumerate(states, 1):
        if cancel is not None and cancel.is_set():          # app is closing: stop cleanly between files
            break
        if progress:
            progress(i - 1, n_files, f"Sanitising {i} of {n_files}")
        outcome = FileOutcome(st.rel)
        try:
            loaded = documents.load_document(st.path, ocr, ocr_languages)
            analysis = redaction.analyze(engine, loaded.text, region, presets, (), False, "", None, disabled)
            for d in analysis.detections:       # safe default: unsure items are hidden, not left in
                if d.uncertain and not d.included:
                    d.included = True
                    outcome.uncertain_hidden += 1
            outcome.hidden = sum(1 for d in analysis.detections if d.included)
            text = analysis.render(shared).text
            outcome.output = f"Document-{i:03d}.txt"
            outcome.ocr_pages = loaded.ocr_pages
            with open(os.path.join(out_dir, outcome.output), "w", encoding="utf-8") as f:
                f.write(header + text + "\n")
            manifest.files[st.rel] = {
                "size": st.size, "mtime_ns": st.mtime_ns, "sha256": st.sha256, "batch": batch_id,
                "output": outcome.output, "hidden": outcome.hidden, "ts": time.time()}
        except Exception as e:                  # one bad file must not stop the batch
            outcome.status, outcome.error = "failed", f"{type(e).__name__}: {e}"
        result.outcomes.append(outcome)

    if progress:
        progress(n_files, n_files, "Finishing")

    report = {"batch": batch_id, "created": datetime.now().isoformat(timespec="seconds"), "folder": manifest.folder,
              "files": [{"source": o.rel, "output": o.output, "status": o.status, "hidden": o.hidden,
                         "uncertain_hidden": o.uncertain_hidden, "ocr_pages": o.ocr_pages, "error": o.error}
                        for o in result.outcomes]}
    with open(os.path.join(local_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)

    if keep_key and key_store and shared["mapping"]:
        key = secrets.token_bytes(32)
        key_store(batch_id, key)
        with open(os.path.join(local_dir, "mapping.enc"), "wb") as f:
            f.write(encrypt_mapping(shared["mapping"], key))
        result.key_kept = True

    manifest.batches.append({"id": batch_id, "ts": time.time(), "files": len(states), "key": result.key_kept})
    manifest.save()
    return result


def apply_renames_and_removals(manifest: Manifest, states) -> None:
    """Keep the manifest in step with the folder without re-processing unchanged content."""
    for st in states:
        if st.status == STATUS_RENAMED and st.prev_rel in manifest.files:
            entry = manifest.files.pop(st.prev_rel)
            entry.update(size=st.size, mtime_ns=st.mtime_ns)
            manifest.files[st.rel] = entry
        elif st.status == STATUS_MISSING:
            manifest.files.pop(st.rel, None)
        elif st.status == STATUS_UNCHANGED and st.rel in manifest.files:
            manifest.files[st.rel].update(size=st.size, mtime_ns=st.mtime_ns)
    manifest.save()


def load_batch_mapping(batch_id: str, key_loader, local_root=None):
    """Decrypt the mapping of a batch that was run with 'keep a key'. Returns None if there is none."""
    path = os.path.join(local_root or os.path.join(base_dir(), "batches"), batch_id, "mapping.enc")
    key = key_loader(batch_id)
    if not (os.path.exists(path) and key):
        return None
    with open(path, "rb") as f:
        return decrypt_mapping(f.read(), key)


def list_restorable_batches(local_root=None) -> list:
    root = local_root or os.path.join(base_dir(), "batches")
    if not os.path.isdir(root):
        return []
    return sorted((b for b in os.listdir(root) if os.path.exists(os.path.join(root, b, "mapping.enc"))), reverse=True)
