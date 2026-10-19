"""
Aegis redaction engine: detection, deterministic tagging, re-hydration.

No Qt imports here, so it is unit-testable and can run in a worker thread.
State lives in RedactionResult objects, never in the shared analyzer registry
(the v0.9.5 design mutated the registry on every run, so manual and preset
rules leaked between documents).
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

# Region label (UI) -> NLP language used for name/location detection.
REGION_LANG = {
    "Portugal (PT)": "pt",
    "Spain (ES)": "es",
    "Germany (DE)": "de",
    "United Kingdom (UK)": "en",
    "Global / USA": "en",
}

AUTO_REGION = "Automatic (recommended)"
LANG_NAMES = {"en": "English", "pt": "Portuguese", "es": "Spanish", "de": "German"}

# What the user can switch on or off in Settings. Anything Presidio reports that is not listed
# here counts as an identifier ("ids"), so a new detector is never silently ignored.
CATEGORIES = {
    "names": ("Names", ["PERSON"], True),
    "places": ("Places and addresses", ["LOCATION"], True),
    "organisations": ("Organisations", ["ORGANIZATION"], True),
    "contact": ("Email addresses and phone numbers", ["EMAIL_ADDRESS", "PHONE_NUMBER"], True),
    "financial": ("Bank and card numbers", ["IBAN_CODE", "CREDIT_CARD", "US_BANK_NUMBER", "CRYPTO"], True),
    "ids": ("Government, tax and health IDs", ["PT_NIF", "PT_CITIZEN_CARD", "ES_DNI_NIE", "ES_NIF", "ES_NIE",
                                               "US_SSN", "US_ITIN", "US_PASSPORT", "US_DRIVER_LICENSE",
                                               "UK_NHS", "UK_NINO", "MEDICAL_LICENSE"], True),
    "web": ("Web addresses and IP addresses", ["URL", "IP_ADDRESS"], True),
    "groups": ("Nationality, religion or political group", ["NRP"], True),
    "dates": ("Dates and times", ["DATE_TIME"], False),
}


def category_of(etype: str):
    """Settings category of an entity type; None for the user's own terms (never switched off)."""
    if etype in ("PRESET", "MANUAL", "TAGLIKE"):
        return None
    for key, (_, types, _default) in CATEGORIES.items():
        if etype in types:
            return key
    return "ids"


def default_disabled() -> frozenset:
    return frozenset(k for k, (_, _t, on) in CATEGORIES.items() if not on)


MODEL_PREFS = {
    "en": ["en_core_web_lg", "en_core_web_md", "en_core_web_sm"],
    "pt": ["pt_core_news_lg", "pt_core_news_md", "pt_core_news_sm"],
    "es": ["es_core_news_lg", "es_core_news_md", "es_core_news_sm"],
    "de": ["de_core_news_lg", "de_core_news_md", "de_core_news_sm"],
}

# Redacted by default. Dates are left alone unless the user opts in: they were the
# main source of leftover "[Date]" placeholders in v0.9.5 and are rarely identifying alone.
DEFAULT_EXCLUDED = {"DATE_TIME"}

# Presidio scores some real identifiers low (phones 0.4, SSN patterns 0.3), so a single
# global threshold either misses them or floods the output. Per-entity minimums instead.
BASE_THRESHOLD = 0.3
DEFAULT_MIN_SCORE = 0.5
ENTITY_MIN_SCORE = {"PHONE_NUMBER": 0.4, "US_SSN": 0.3}

# Short field labels that spaCy small models routinely tag as ORG ("NIF", "SSN").
LABEL_STOPLIST = {"NIF", "NIPC", "SSN", "DNI", "NIE", "IBAN", "CC", "IVA", "VAT", "TAX", "ID", "NHS", "NINO"}
MAX_CHUNK = 150_000


class EngineError(RuntimeError):
    pass


# --------------------------------------------------------------------------- validators
def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def valid_pt_nif(raw: str) -> bool:
    d = _digits(raw)
    if len(d) != 9:
        return False
    if not (d[0] in "12356789" or d[:2] == "45"):
        return False
    total = sum(int(d[i]) * (9 - i) for i in range(8))
    check = 11 - (total % 11)
    if check >= 10:
        check = 0
    return check == int(d[8])


_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def valid_es_dni_nie(raw: str) -> bool:
    s = raw.strip().upper()
    if len(s) < 8:
        return False
    prefix = {"X": "0", "Y": "1", "Z": "2"}.get(s[0])
    body = (prefix + s[1:-1]) if prefix is not None else s[:-1]
    if not body.isdigit():
        return False
    return _DNI_LETTERS[int(body) % 23] == s[-1]


def valid_pt_cc(raw: str) -> bool:
    s = re.sub(r"[\s-]", "", raw).upper()
    if len(s) != 12:
        return False
    total, second = 0, False
    for ch in reversed(s):
        v = int(ch, 36)
        if second:
            v *= 2
            if v > 9:
                v -= 9
        total += v
        second = not second
    return total % 10 == 0


# --------------------------------------------------------------------------- language
def detect_language(text: str, available) -> str | None:
    """Return a language code from `available` when the text is clearly in it, else None."""
    sample = (text or "")[:5000]
    if len(sample.strip()) < 20:
        return None
    try:
        from langdetect import DetectorFactory, detect_langs
        DetectorFactory.seed = 0           # deterministic
        for item in detect_langs(sample):
            if item.lang in available and item.prob >= 0.7:
                return item.lang
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- engine
@dataclass
class Engine:
    analyzer: object
    languages: list
    models: dict  # lang -> spaCy model name actually loaded


def installed_models() -> dict:
    import spacy.util

    found = {}
    for lang, candidates in MODEL_PREFS.items():
        for name in candidates:
            if spacy.util.is_package(name):
                found[lang] = name
                break
    return found


def build_engine() -> Engine:
    from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import (
        CreditCardRecognizer, EmailRecognizer, IbanRecognizer, IpRecognizer,
        PhoneRecognizer, UrlRecognizer,
    )

    # tldextract (used by the URL recognizer) downloads the public-suffix list at runtime.
    # A privacy gateway must not make hidden network calls: use the bundled snapshot only.
    try:
        import tldextract
        tldextract.tldextract.TLD_EXTRACTOR = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
    except Exception:
        pass

    models = installed_models()
    if "en" not in models:
        raise EngineError(
            "No English spaCy model is installed. Run: python -m spacy download en_core_web_lg"
        )
    langs = list(models)
    conf = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": l, "model_name": m} for l, m in models.items()],
    }
    nlp = NlpEngineProvider(nlp_configuration=conf).create_engine()
    registry = RecognizerRegistry(supported_languages=langs)
    registry.load_predefined_recognizers(languages=langs, nlp_engine=nlp)

    class _Checked(PatternRecognizer):
        """Valid checksum -> score 1.0. Invalid -> keep low base score, so a bare
        look-alike number is dropped but one next to a context word ("NIF") survives."""

        def __init__(self, entity, regex, validator, context, lang, base=0.3):
            super().__init__(
                supported_entity=entity,
                name=f"{entity}_{lang}",
                supported_language=lang,
                patterns=[Pattern(entity.lower(), regex, base)],
                context=context,
            )
            self._validator = validator

        def validate_result(self, pattern_text):
            return True if self._validator(pattern_text) else None

    have = {(r.supported_entities[0], r.supported_language) for r in registry.recognizers}
    for lang in langs:
        # Generic recognizers that Presidio only registers for some languages.
        generic = [
            ("EMAIL_ADDRESS", lambda l=lang: EmailRecognizer(supported_language=l)),
            ("CREDIT_CARD", lambda l=lang: CreditCardRecognizer(supported_language=l)),
            ("IBAN_CODE", lambda l=lang: IbanRecognizer(supported_language=l)),
            ("IP_ADDRESS", lambda l=lang: IpRecognizer(supported_language=l)),
            ("URL", lambda l=lang: UrlRecognizer(supported_language=l)),
            ("PHONE_NUMBER", lambda l=lang: PhoneRecognizer(
                supported_language=l, supported_regions=["PT", "ES", "DE", "GB", "US", "FR", "IT", "BR"])),
        ]
        for entity, make in generic:
            if (entity, lang) not in have:
                registry.add_recognizer(make())

        registry.add_recognizer(_Checked(
            "PT_NIF", r"(?<!\d)[1-9]\d{2}[ .-]?\d{3}[ .-]?\d{3}(?!\d)", valid_pt_nif,
            ["NIF", "NIPC", "contribuinte", "n.º", "tax"], lang))
        registry.add_recognizer(_Checked(
            "PT_CITIZEN_CARD", r"(?<![A-Za-z0-9])\d{8}[ -]?\d[ -]?[A-Za-z0-9]{2}\d(?![A-Za-z0-9])", valid_pt_cc,
            ["Cartão de Cidadão", "cidadão", "BI", "documento de identificação", "CC"], lang, base=0.3))
        registry.add_recognizer(PatternRecognizer(
            supported_entity="PHONE_NUMBER", name=f"PT_PHONE_{lang}", supported_language=lang,
            patterns=[Pattern("pt_phone_intl", r"(?<!\d)\+351[ .-]?\d{3}[ .-]?\d{3}[ .-]?\d{3}(?!\d)", 0.85),
                      Pattern("pt_phone", r"(?<!\d)(?:9[1236]\d|2\d{2})[ .-]?\d{3}[ .-]?\d{3}(?!\d)", 0.5)]))
        registry.add_recognizer(PatternRecognizer(
            supported_entity="PHONE_NUMBER", name=f"ES_PHONE_{lang}", supported_language=lang,
            patterns=[Pattern("es_phone_intl", r"(?<!\d)\+34[ .-]?\d{3}[ .-]?\d{3}[ .-]?\d{3}(?!\d)", 0.85),
                      Pattern("es_phone", r"(?<!\d)[67]\d{2}[ .-]?\d{3}[ .-]?\d{3}(?!\d)", 0.5)]))
        registry.add_recognizer(_Checked(
            "ES_DNI_NIE", r"(?<![A-Za-z0-9])[XYZxyz]?\d{7,8}[A-Za-z](?![A-Za-z0-9])", valid_es_dni_nie,
            ["DNI", "NIE", "NIF", "documento"], lang))

    analyzer = AnalyzerEngine(
        registry=registry, nlp_engine=nlp, supported_languages=langs, default_score_threshold=0.0)
    return Engine(analyzer=analyzer, languages=langs, models=models)


# --------------------------------------------------------------------------- redaction
@dataclass
class Span:
    start: int
    end: int
    etype: str
    score: float


@dataclass
class RedactionResult:
    text: str
    mapping: dict = field(default_factory=dict)  # "<PERSON_1>" -> original value
    counts: dict = field(default_factory=dict)   # "PERSON" -> number of distinct values
    language: str = "en"
    degraded: bool = False                       # selected language had no model
    uncertain: int = 0                           # items the user should look at (see Analysis)


def _norm(v: str) -> str:
    return re.sub(r"\s+", " ", v.strip()).casefold()


def _term_spans(text: str, terms, etype: str):
    for term in terms:
        t = term.strip()
        if not t:
            continue
        for m in re.finditer(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text, re.IGNORECASE):
            yield Span(m.start(), m.end(), etype, 1.0)


def _chunks(text: str):
    """Yield (offset, chunk), splitting on newlines so entities are not cut in half."""
    pos, n = 0, len(text)
    while pos < n:
        end = min(pos + MAX_CHUNK, n)
        if end < n:
            nl = text.rfind("\n", pos, end)
            if nl > pos:
                end = nl + 1
        yield pos, text[pos:end]
        pos = end


_NER_TYPES = {"PERSON", "LOCATION", "ORGANIZATION", "NRP", "DATE_TIME"}


def _tier(s: "Span") -> int:
    """Explicit user terms beat pattern/checksum detectors, which beat statistical NER.
    Small spaCy models often tag URLs and codes as PERSON; the pattern match is the better call."""
    if s.etype in _TIER0:
        return 0
    return 2 if s.etype in _NER_TYPES else 1


def resolve_overlaps(spans):
    """Greedy by (tier, score, length) with a correct interval-overlap test."""
    chosen = []
    for s in sorted(spans, key=lambda x: (_tier(x), -x.score, -(x.end - x.start), x.start)):
        if all(s.end <= c.start or s.start >= c.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda x: x.start)


NEEDS_ATTENTION_BELOW = 0.7    # included detections under this score are flagged for a look
INSTRUCTION_NER_ABOVE = 300    # a long message is pasted content, so it gets full name detection


@dataclass
class Detection:
    """One candidate span, as shown (and editable) in the inspector."""
    id: int
    segment: str        # "doc" | "instr"
    start: int
    end: int
    etype: str
    score: float
    source: str         # "model" | "pattern" | "preset" | "manual" | "heuristic"
    text: str
    included: bool
    uncertain: bool = False
    reason: str = ""


_POSSIBLE_NAME = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]{2,}(?:\s+[A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ]+)*")


def _raw_candidates(engine: Engine, text: str, language: str):
    out = []
    for offset, chunk in _chunks(text):
        for r in engine.analyzer.analyze(text=chunk, language=language, score_threshold=BASE_THRESHOLD):
            if chunk[r.start:r.end].strip().upper() in LABEL_STOPLIST:
                continue
            out.append(Span(r.start + offset, r.end + offset, r.entity_type, r.score))
    return out


def _overlaps(s: Span, chosen) -> bool:
    return any(not (s.end <= c.start or s.start >= c.end) for c in chosen)


def _source(etype: str) -> str:
    if etype == "PRESET":
        return "preset"
    if etype == "MANUAL":
        return "manual"
    return "model" if etype in _NER_TYPES else "pattern"


_TIER0 = ("MANUAL", "PRESET", "TAGLIKE")


def _classify(engine, text, language, is_off, allow_ner, presets, manual, segment, ids):
    """Turn raw analyzer output into Detections: what is redacted, what is merely suspected."""
    raw = _raw_candidates(engine, text, language)
    if not allow_ner:
        raw = [x for x in raw if x.etype not in _NER_TYPES]
    eligible, lowconf, category_off = [], [], []
    for x in raw:
        if is_off(x.etype):
            category_off.append(x)
        elif x.score < ENTITY_MIN_SCORE.get(x.etype, DEFAULT_MIN_SCORE):
            lowconf.append(x)
        else:
            eligible.append(x)
    terms = list(_term_spans(text, presets, "PRESET")) + list(_term_spans(text, manual, "MANUAL"))
    # Text that already contains placeholder-looking tokens (e.g. a re-pasted sanitised note) would be
    # swapped for real values when the answer comes back. Re-tag those tokens so they cannot collide.
    terms += [Span(m.start(), m.end(), "TAGLIKE", 1.0) for m in re.finditer(r"<[A-Z][A-Z_]*_\d+>", text)]
    chosen = resolve_overlaps(eligible + terms)
    detections = []

    def make(x, included, uncertain, reason):
        detections.append(Detection(next(ids), segment, x.start, x.end, x.etype, x.score,
                                    _source(x.etype), text[x.start:x.end], included, uncertain, reason))

    for x in chosen:
        weak = x.score < NEEDS_ATTENTION_BELOW and x.etype not in _TIER0
        make(x, True, weak, "Matched on a nearby label, not a verified value" if weak else "")
    rest = [x for x in lowconf + category_off if not _overlaps(x, chosen)]
    for x in resolve_overlaps(rest):
        if is_off(x.etype):
            make(x, False, False, "Switched off in Settings")
        else:
            make(x, False, True, f"Low confidence: may be a {x.etype.replace('_', ' ').lower()}")
    return detections, chosen


@dataclass
class Analysis:
    """Everything detected, editable, and renderable. The inspector edits this object."""
    doc: str
    instruction: str
    detections: list
    presets: tuple
    manual: tuple
    language: str
    degraded: bool
    language_source: str = "chosen"     # "detected" | "chosen" | "default"
    _ids: object = field(default=None, repr=False)

    def by_id(self, det_id: int) -> Detection:
        return next(d for d in self.detections if d.id == det_id)

    def set_included(self, det_id: int, included: bool):
        d = self.by_id(det_id)
        d.included = included
        if included:  # an explicit user decision resolves the doubt
            d.uncertain = False

    def needs_attention(self):
        return [d for d in self.detections if d.uncertain]

    def add_term(self, term: str, etype: str = "MANUAL") -> int:
        """Redact every occurrence of term; anything it overlaps is switched off in its favour."""
        added = 0
        for segment, text in (("doc", self.doc), ("instr", self.instruction)):
            for sp in _term_spans(text, [term], etype):
                for d in self.detections:
                    if d.segment == segment and d.included and not (sp.end <= d.start or sp.start >= d.end):
                        d.included = False
                self.detections.append(Detection(
                    next(self._ids), segment, sp.start, sp.end, etype, 1.0, _source(etype),
                    text[sp.start:sp.end], True))
                added += 1
        return added

    def render(self, state=None) -> RedactionResult:
        """Apply the included detections. Pass a shared `state` (new_state()) to keep tags consistent
        across several documents: the same value then gets the same placeholder in every file."""
        state = state if state is not None else new_state()
        pieces = {}
        derived = [(d.etype, d.text) for d in self.detections if d.segment == "doc" and d.included]
        for segment, text in (("doc", self.doc), ("instr", self.instruction)):
            if not text:
                pieces[segment] = ""
                continue
            spans = [Span(d.start, d.end, d.etype, d.score)
                     for d in self.detections if d.segment == segment and d.included]
            if segment == "instr":  # values already redacted in the document are redacted here too
                for etype, value in derived:
                    spans += list(_term_spans(text, [value], etype))
            pieces[segment] = _apply(text, resolve_overlaps(spans), state)
        if self.doc and self.instruction:
            out = f"{pieces['instr']}\n\n---\n\n{pieces['doc']}"
        else:
            out = pieces["doc"] or pieces["instr"]
        return RedactionResult(out, state["mapping"], state["counts"], self.language, self.degraded,
                               len(self.needs_attention()))


def new_state() -> dict:
    return {"by_key": {}, "mapping": {}, "counts": {}}


def _apply(text, spans, state):
    """Replace spans with deterministic tags. Same value + type -> same tag, across segments."""
    out, cursor = [], 0
    for sp in spans:
        original = text[sp.start:sp.end]
        key = (sp.etype, _norm(original))
        tag = state["by_key"].get(key)
        if tag is None:
            state["counts"][sp.etype] = state["counts"].get(sp.etype, 0) + 1
            tag = f"<{sp.etype}_{state['counts'][sp.etype]}>"
            state["by_key"][key] = tag
            state["mapping"][tag] = original
        out.append(text[cursor:sp.start])
        out.append(tag)
        cursor = sp.end
    out.append(text[cursor:])
    return "".join(out)


def analyze(engine: Engine, doc: str, region: str = "Global / USA", presets=(), manual=(),
            redact_dates: bool = False, instruction: str = "", language: str | None = None,
            disabled=None) -> Analysis:
    """Detect once; the user can then edit detections and call render() as often as needed.

    Language: an explicit `language` code wins; region == AUTO_REGION detects it from the text;
    otherwise the region picks it. `disabled` is the set of switched-off categories (see CATEGORIES).

    The instruction skips statistical NER when it is short and a document is attached: small models
    tag whole phrases like "Summarise for Ana" as a PERSON. Instead it is covered by pattern detectors,
    presets, manual terms, values found in the document, and capitalised words flagged as possible names.
    """
    instruction = (instruction or "").strip()
    source = "chosen"
    if language:
        wanted = language
    elif region == AUTO_REGION:
        found = detect_language(f"{doc}\n{instruction}", engine.languages)
        wanted, source = (found, "detected") if found else ("en", "default")
    else:
        wanted = REGION_LANG.get(region, "en")
    degraded = False
    if wanted not in engine.languages:
        wanted, degraded = "en", True

    if disabled is None:
        disabled = default_disabled() if not redact_dates else frozenset()
    disabled = frozenset(disabled)
    is_off = lambda etype: category_of(etype) in disabled          # noqa: E731
    ids = itertools.count()
    detections = []

    if doc:
        d, _ = _classify(engine, doc, wanted, is_off, True, presets, manual, "doc", ids)
        detections += d
    if instruction:
        allow_ner = (not doc) or len(instruction) > INSTRUCTION_NER_ABOVE
        d, chosen = _classify(engine, instruction, wanted, is_off, allow_ner, presets, manual, "instr", ids)
        detections += d
        if not allow_ner and "names" not in disabled:
            known = [(x.start, x.end) for x in d if x.included]
            for m in _POSSIBLE_NAME.finditer(instruction):
                if any(not (m.end() <= a or m.start() >= b) for a, b in known):
                    continue
                detections.append(Detection(
                    next(ids), "instr", m.start(), m.end(), "PERSON", 0.4, "heuristic", m.group(0),
                    False, True, "Capitalised word in your request: could be a name"))
    return Analysis(doc, instruction, detections, tuple(presets), tuple(manual), wanted, degraded, source, ids)


def redact(engine: Engine, text: str, region: str = "Global / USA", presets=(), manual=(),
           redact_dates: bool = False, instruction: str = "") -> RedactionResult:
    """One-shot helper (tests, scripts): analyse and render with default decisions."""
    return analyze(engine, text, region, presets, manual, redact_dates, instruction).render()


# --------------------------------------------------------------------------- re-hydration
_TAG_RE = re.compile(r"<\s*([A-Za-z][A-Za-z_ ]*?)[\s_]*(\d+)\s*>")
_BRACKET_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9 ./_-]{1,40})\](?!\()")


def rehydrate(text: str, mapping: dict):
    """Return (text, unresolved_tags). Tolerates <Person 1>, <person_1>, <PERSON__1>."""
    known_types = {re.sub(r"_\d+>$", "", k)[1:] for k in mapping}
    unresolved = []

    def sub(m):
        etype = re.sub(r"\s+", "_", m.group(1).strip()).upper()
        key = f"<{etype}_{m.group(2)}>"
        if key in mapping:
            return mapping[key]
        if etype in known_types:
            unresolved.append(key)
        return m.group(0)

    out = _TAG_RE.sub(sub, text)
    return out, sorted(set(unresolved))


def find_fill_candidates(text: str):
    """Unresolved <TAG_n> tags and [Bracket] placeholders, in order, de-duplicated."""
    found = [m.group(0) for m in re.finditer(r"<[A-Z][A-Z_]*_\d+>", text)]
    found += [m.group(0) for m in _BRACKET_RE.finditer(text)]
    return list(dict.fromkeys(found))
