# Third-party licences (checked from installed package metadata, 2026-10)

| Component | Licence | Commercial note |
|---|---|---|
| presidio-analyzer / anonymizer | MIT | fine |
| spaCy | MIT | fine |
| spaCy `en_core_web_*` models | MIT | fine |
| spaCy `pt_core_news_*` models | **CC BY-SA 4.0** | share-alike: have a lawyer confirm what bundling it in a closed commercial app requires |
| spaCy `es_/de_core_news_*` models | check before bundling | licences differ per language; verify on spaCy's model pages |
| langdetect, keyring, beautifulsoup4, python-docx | MIT | fine |
| pypdf | BSD-3-Clause | fine |
| PySide6 (Qt) | LGPL-3.0 (or GPL / commercial) | allowed for closed apps if users can relink Qt: prefer a folder-based (non-onefile) build, or buy a Qt licence |
| google-genai | check (not installed where this was audited) | verify |

Avoid PyMuPDF for PDF rendering (AGPL); use pypdfium2 (Apache-2.0/BSD) when OCR for scanned PDFs is added.
This is an engineering inventory, not legal advice.
