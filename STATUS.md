# Aegis Privacy Airlock: project status (update this file with every release)

**Current version:** 0.13.3   **Target:** macOS first (Apple Vision OCR, Keychain); corporate/cross-platform later.
**Author's Mac:** macOS 12.7.6, Intel, Python 3.11.9.

## Verified where
| Area | Linux sandbox (tests) | Author's Mac |
|---|---|---|
| Redaction, batches, settings, mail logic, UI flows (86 tests) | yes | yes (85 passed, 1 skipped) |
| Apple Vision OCR on image and PDF | no (Tesseract only) | yes (sample page read; small misreads on ç and º) |
| Core Graphics PDF renderer | no | partly: run `python tools/render_check.py tools/sample_scan.pdf` |
| Signed .app build, notarisation, Keychain prompts | no | not tested |

## Open items
- PDF read dropped the "Total" line and part of "Atlas" on the Mac (PNG did not): check renderer output.
- Not built: follow-up messages in a thread, OAuth for Outlook/Microsoft 365, audit-log export, folder copies keep only plain text.
- spaCy Portuguese model is CC BY-SA 4.0: legal check before distribution (see LICENSES.md).

## Release checklist
1. `python tools/assemble_project.py` prints RESULT: OK (no stale or missing files, versions agree).
2. pyflakes clean; fast tests (core, v12, v13, robustness) pass; UI-flow tests pass.
3. On the Mac: `python tools/ocr_check.py tools/sample_scan.pdf` and `.png` read all lines.
4. Run the app once end to end with a synthetic document and a synthetic folder.
5. Build, sign, notarise on a clean Mac (`build_app.command`); legal review of model licences and marketing wording.

## Never put in the Project
Real documents, ID scans, API keys, passwords, logs, exported diagnostics, batch manifests or mapping files.
