"""State of one task: what the user attached, what was detected, what was sent, what came back."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Attachment:
    kind: str    # "document" | "inbox"
    name: str    # shown only on this Mac (chips); never sent
    text: str
    ocr_pages: int = 0

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class Session:
    attachments: list = field(default_factory=list)
    message: str = ""
    analysis: object = None        # redaction.Analysis (editable until sent)
    result: object = None          # latest render of the analysis
    sent_result: object = None     # frozen snapshot of what was transmitted; re-hydration uses this
    sent: bool = False
    raw_response: str = ""
    final_text: str = ""
    model: str = ""

    def compose_doc(self) -> str:
        """Attachments joined with generic labels. Real file names may contain personal data,
        so they are never part of the text that is analysed or sent."""
        parts = []
        for i, a in enumerate(self.attachments, 1):
            label = "Inbox digest" if a.kind == "inbox" else f"Document {i}"
            parts.append(f"[{label}]\n{a.text.strip()}")
        return "\n\n".join(parts)
