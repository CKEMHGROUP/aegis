# Aegis Privacy Airlock v0.13.3  (includes everything from v0.11 to v0.13)

## Run (macOS), one line at a time
```
cd ~/Desktop/AegisPrivacyAirlock_v0.13.3
source ~/venv/bin/activate
pip install -r requirements.txt        # adds pypdfium2, pillow, pyobjc Vision/Quartz, langdetect, pytesseract
python tools/ocr_check.py              # shows your macOS version, chip and which OCR engine will be used
python tools/ocr_check.py ~/Desktop/some-scan.pdf     # optional: reads a real scan
python -m pytest -q tests -k "not ui_flows"   # 70 tests, about 1 minute
python -m pytest -q tests              # all 86 tests, about 7 minutes (they drive the real window)
python app.py
```
`sw_vers` and `uname -m` (macOS version and chip) are also printed by `tools/ocr_check.py`, so you need not run them.

## Two modes (top bar): Ask | Folders  (Ctrl+1 / Ctrl+2)
**Ask**: chat-style composer, review card, inspector. **Folders**: drop a folder anywhere in the window.

### Folders: one-way shareable copies
- Every new or changed file is turned into `Document-001.txt`, `Document-002.txt` ... in
  `~/Documents/Aegis Sanitised/Sanitised <batch id>/` (change in Settings > Documents & Folders).
- The shared folder contains **no original file names, no manifest, no mapping**, and each file starts with a
  "sanitised copy, one-way" header. Originals are never changed.
- Same person, same placeholder across all files of a batch, so a colleague can follow who is who.
- Items the detector is unsure about are **hidden** in batches (safe default). The summary says how many.
- Compare: the folder is re-checked on drop, on Rescan and every 15 s while the view is open. Statuses:
  New · Changed · Unchanged · Renamed · Removed. A file that was only touched (same content) is not a change.
- Optional per batch: "Keep an encrypted key to restore real values later". Off by default. When on, an AES-GCM
  encrypted mapping stays on THIS Mac and its key goes to the Keychain. "Restore a returned file…" puts real
  values back into a file a colleague sent back. Without the key nobody can reverse a copy.
- Double-click a row to preview exactly what a colleague would see.

### OCR
Scanned PDF pages, photos and screenshots (png, jpg, tiff, heic) are read on this Mac: Apple Vision first, Tesseract as
fallback. Page images are processed in memory, never written to disk. Toggle in Settings > Documents & Folders.
OCR can misread digits; the review card says when OCR was used.

## Important limits (read this)
- Sanitising is **not access control**. A copy can still identify someone to a reader who knows the context.
  Department clearance must stay enforced by your permissions, not by this tool.
- Folder copies are plain `.txt` for now (no layout, tables or images from the original).
- Not verified on macOS by the author: Apple Vision, signing, the packaged app. Headless Linux tests with Tesseract only.
- Language models: see `LICENSES.md` (Portuguese model is CC BY-SA).

## v0.13.2: audit results (86 automated tests, all passing)
Found and fixed:
1. Scanned-PDF OCR needed `pypdfium2`, which cannot install on macOS 12 Intel without Xcode tools. A Mac now renders pages
   with built-in Core Graphics (`aegis/pdfrender.py`); pypdfium2 is only an optional fallback.
2. A file that failed (e.g. a corrupt PDF) was retried automatically in a second batch. It is now retried only when its
   content changes, or when you press the Sanitise button.
3. Previewing a file from an earlier batch showed nothing. Previews now come from the manifest, so they also work after a restart.
4. Quitting while a folder batch ran could crash the app. Closing now cancels the batch between files and waits for threads.
5. Text that already contained placeholders like `<PERSON_1>` (a re-pasted sanitised note) could later be swapped for a real
   name. Such tokens are now re-tagged so they cannot collide.
6. Crash at exit after dropping files in tests / quit order of widgets (v0.13.1).
Still unverified on a real Mac: the Core Graphics page renderer and Apple Vision on an actual scan. Run:
`python tools/ocr_check.py ~/Desktop/some-scan.pdf` and send me the output.
