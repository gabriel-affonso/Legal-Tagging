"""Step 3.7 focused, evidence-first contract extraction.

The module deliberately has no Ollama dependency.  It performs the cheap
page-index scan, selects three contract pages plus one useful cadastral page,
builds the bounded prompt context and records deterministic candidates.  The
existing Step 3.6 resolver and Ollama client consume that small context later.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
import re
import unicodedata
from typing import Any, Iterable

from .models import ExtractionResult


_INDICATORS: tuple[tuple[str, str, int, str], ...] = (
    ("caderneta_predial", "Caderneta Predial", 5, "TITLE"),
    ("autoridade_tributaria", "Autoridade Tributaria e Aduaneira", 4, "D"),
    ("identificacao_predio", "Identificacao do Predio", 4, "A"),
    ("artigo_matricial", "Artigo Matricial", 3, "A"),
    ("servico_financas", "Servico de Financas", 3, "D"),
    ("identificacao_titulares", "Identificacao dos Titulares", 3, "C"),
    ("titulares", "Titulares", 2, "C"),
    ("localizacao_predio", "Localizacao do Predio", 2, "B"),
    ("elementos_predio", "Elementos do Predio", 2, "A"),
    ("predio_rustico", "Predio Rustico", 2, "A"),
    ("predio_urbano", "Predio Urbano", 2, "A"),
    ("predio_misto", "Predio Misto", 2, "A"),
    ("freguesia", "Freguesia", 1, "B"),
    ("concelho", "Concelho", 1, "B"),
    ("distrito", "Distrito", 1, "B"),
    ("seccao", "Seccao", 1, "A"),
    ("valor_patrimonial", "Valor patrimonial", 1, "A"),
    ("areas", "Areas", 1, "A"),
    ("ano_inscricao_matriz", "Ano de inscricao na matriz", 1, "A"),
)

_CORE_KEYWORDS_RE = re.compile(
    r"senhori|arrendat|outorgante|propriet|titular|nif|nipc|artigo|sec[cç][aã]o|"
    r"freguesia|concelho|distrito|localiza[cç][aã]o|descri[cç][aã]o|registo|"
    r"pr[eé]dio|matriz|caderneta|autoridade tribut|servi[cç]o de finan[cç]as|data",
    re.I,
)


@dataclass(frozen=True)
class FocusedPage:
    page_number: int
    semantic_role: str
    source: str
    ocr_score: float
    raw_text: str
    normalized_text: str
    context_text: str = ""
    page_context_truncated: bool = False

    def to_dict(self, *, include_text: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "page_number": self.page_number,
            "semantic_role": self.semantic_role,
            "source": self.source,
            "ocr_score": round(self.ocr_score, 3),
            "page_context_truncated": self.page_context_truncated,
        }
        if include_text:
            payload.update(
                raw_text=self.raw_text,
                normalized_text=self.normalized_text,
                context_text=self.context_text,
            )
        return payload


@dataclass(frozen=True)
class CadastralPageCandidate:
    page_number: int
    score: int
    matched_indicators: tuple[str, ...]
    content_groups: tuple[str, ...]
    indicator_diversity: int
    institutional_header: bool
    useful_content: bool
    is_separator: bool
    is_valid: bool
    ocr_quality: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DeterministicCandidate:
    field: str
    value: str
    normalized_value: str
    source_page: int
    semantic_page: str
    source_method: str
    source: str
    evidence_text: str
    start_offset: int
    end_offset: int
    confidence: float
    proximity: str = "direct_label_or_role"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["confidence"] = round(self.confidence, 3)
        return payload


@dataclass
class Step37Report:
    file_name: str
    total_pages: int
    pages: list[FocusedPage] = field(default_factory=list)
    cadastral_candidates: list[CadastralPageCandidate] = field(default_factory=list)
    deterministic_candidates: list[DeterministicCandidate] = field(default_factory=list)
    cadastral_page: int | None = None
    context: str = ""
    document_text: str = ""
    context_truncated: bool = False
    visual_confirmation_required: bool = False
    visual_candidate_pages: list[int] = field(default_factory=list)
    visual_confirmation_reasons: list[str] = field(default_factory=list)
    selected_source_path: str = ""

    @property
    def selected_page_numbers(self) -> list[int]:
        return [page.page_number for page in self.pages]

    def to_dict(self) -> dict[str, object]:
        return {
            "version": "3.7",
            "strategy": "focused_core_evidence",
            "file_name": self.file_name,
            "total_pages": self.total_pages,
            "contract_pages": [
                page.page_number for page in self.pages
                if page.semantic_role.startswith("contract_page_")
            ],
            "cadastral_page": self.cadastral_page,
            "selected_pages": self.selected_page_numbers,
            "selected_source_path": self.selected_source_path,
            "context_chars": len(self.context),
            "context_truncated": self.context_truncated,
            "pages": [page.to_dict() for page in self.pages],
            "cadastral_candidates": [item.to_dict() for item in self.cadastral_candidates],
            "deterministic_candidates": [item.to_dict() for item in self.deterministic_candidates],
            "visual_confirmation": {
                "required": self.visual_confirmation_required,
                "candidate_pages": list(self.visual_candidate_pages),
                "reasons": list(self.visual_confirmation_reasons),
            },
        }


def prepare_step37(pdf_path: Path, *, file_name: str, config: Any) -> Step37Report:
    """Select physical pages and build the only text package used in Step 3.7."""
    source_path, source = _preferred_text_pdf(pdf_path, config)
    raw_pages = read_pdf_pages(source_path)
    report = Step37Report(
        file_name=file_name,
        total_pages=len(raw_pages),
        selected_source_path=str(source_path),
    )
    if not raw_pages:
        return report

    contract_count = min(
        len(raw_pages),
        int(getattr(config, "step_3_7_maximum_contract_pages", 3)),
    )
    total_limit = max(1, int(getattr(config, "step_3_7_maximum_total_pages", 4)))
    contract_count = min(contract_count, total_limit)
    page_records = [
        FocusedPage(
            page_number=index,
            semantic_role=f"contract_page_{index}",
            source=source,
            ocr_score=_text_quality(text),
            raw_text=text,
            normalized_text=_normalize_layout(text),
        )
        for index, text in enumerate(raw_pages[:contract_count], start=1)
    ]

    threshold = int(getattr(config, "step_3_7_cadastral_page_threshold", 7))
    min_diversity = int(
        getattr(config, "step_3_7_cadastral_min_indicator_diversity", 2)
    )
    configured_scores = dict(
        getattr(config, "step_3_7_cadastral_indicator_scores", {}) or {}
    )
    candidates = [
        score_cadastral_page(
            page_number,
            raw_pages[page_number - 1],
            threshold=threshold,
            minimum_diversity=min_diversity,
            indicator_scores=configured_scores,
        )
        for page_number in range(contract_count + 1, len(raw_pages) + 1)
    ]
    report.cadastral_candidates = candidates
    valid = [candidate for candidate in candidates if candidate.is_valid]
    selected = min(valid, key=lambda item: item.page_number) if valid else None
    if (
        selected is not None
        and int(getattr(config, "step_3_7_maximum_cadastral_pages", 1)) > 0
        and len(page_records) < total_limit
    ):
        report.cadastral_page = selected.page_number
        text = raw_pages[selected.page_number - 1]
        page_records.append(
            FocusedPage(
                page_number=selected.page_number,
                semantic_role="cadastral_page_1",
                source=source,
                ocr_score=_text_quality(text),
                raw_text=text,
                normalized_text=_normalize_layout(text),
            )
        )

    _plan_visual_confirmation(report, candidates, config)
    report.deterministic_candidates = extract_deterministic_candidates(page_records)
    report.pages, report.context, report.context_truncated = _build_context(
        report.file_name,
        report.total_pages,
        page_records,
        deterministic_candidates=report.deterministic_candidates,
        max_chars_per_page=int(
            getattr(config, "step_3_7_maximum_chars_per_page", 12_000)
        ),
        max_total_chars=int(
            getattr(config, "step_3_7_maximum_total_context_chars", 40_000)
        ),
    )
    report.document_text = "\n\n".join(
        f"[Page {page.page_number}]\n{page.context_text}"
        for page in report.pages
        if page.context_text.strip()
    )
    return report


def read_pdf_pages(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is mandatory
        raise RuntimeError("Missing dependency: install pypdf") from exc
    reader = PdfReader(str(path))
    return [(page.extract_text() or "").replace("\x00", "") for page in reader.pages]


def score_cadastral_page(
    page_number: int,
    text: str,
    *,
    threshold: int = 7,
    minimum_diversity: int = 2,
    indicator_scores: dict[str, int] | None = None,
) -> CadastralPageCandidate:
    searchable = _comparison_text(text)
    custom = {_comparison_text(key): int(value) for key, value in (indicator_scores or {}).items()}
    matched: list[str] = []
    groups: set[str] = set()
    score = 0
    for key, phrase, default_weight, group in _INDICATORS:
        if _contains_indicator(searchable, _comparison_text(phrase)):
            matched.append(key)
            groups.add(group)
            score += custom.get(_comparison_text(key), custom.get(_comparison_text(phrase), default_weight))

    # The location pair is a stronger structural signal than either generic
    # word by itself, matching the score described in the Step 3.7 design.
    if "freguesia" in matched and "concelho" in matched:
        score += 2
    content_groups = tuple(sorted(group for group in groups if group in {"A", "B", "C", "D"}))
    useful = len(content_groups) >= 2
    separator = "caderneta_predial" in matched and not useful
    diversity = len(matched)
    is_valid = score >= threshold and diversity >= minimum_diversity and useful and not separator
    return CadastralPageCandidate(
        page_number=page_number,
        score=score,
        matched_indicators=tuple(matched),
        content_groups=content_groups,
        indicator_diversity=diversity,
        institutional_header=bool({"autoridade_tributaria", "servico_financas"} & set(matched)),
        useful_content=useful,
        is_separator=separator,
        is_valid=is_valid,
        ocr_quality=_text_quality(text),
    )


def extract_deterministic_candidates(
    pages: Iterable[FocusedPage],
) -> list[DeterministicCandidate]:
    candidates: list[DeterministicCandidate] = []
    seen: set[tuple[str, str, int]] = set()
    for page in pages:
        cadastral = page.semantic_role == "cadastral_page_1"
        patterns: tuple[tuple[str, re.Pattern[str], float], ...] = (
            ("property_article", re.compile(r"\bartigo(?:\s+matricial)?\s*(?:n[.ºo°]\s*)?[:#-]?\s*(?P<value>\d{1,8})\b", re.I), 0.98 if cadastral else 0.91),
            ("property_section", re.compile(r"\bsec(?:c|ç)[aã]o\s*[:#-]?\s*(?P<value>[A-Z]{1,3})\b", re.I), 0.98 if cadastral else 0.90),
            ("property_parish", re.compile(r"\bfreguesia\s*[:#-]?\s*(?P<value>[^\n,;|]{2,80})", re.I), 0.96 if cadastral else 0.86),
            ("property_municipality", re.compile(r"\b(?:concelho|munic[ií]pio)\s*[:#-]?\s*(?P<value>[^\n,;|]{2,80})", re.I), 0.96 if cadastral else 0.86),
            ("property_district", re.compile(r"\bdistrito\s*[:#-]?\s*(?P<value>[^\n,;|]{2,80})", re.I), 0.95 if cadastral else 0.84),
            ("property_location", re.compile(r"\b(?:nome\s*/\s*)?localiza[cç][aã]o(?:\s+do\s+pr[eé]dio)?\s*[:#-]?\s*(?P<value>[^\n;|]{3,120})", re.I), 0.95 if cadastral else 0.84),
            ("registry_description", re.compile(r"\bdescri[cç][aã]o\s+predial\s*(?:n[.ºo°]\s*)?[:#-]?\s*(?P<value>[^\n;|]{1,100})", re.I), 0.94),
            ("registry_number", re.compile(r"\b(?:n[uú]mero\s+de\s+registo|registo\s+n[.ºo°])\s*[:#-]?\s*(?P<value>[^\n;|]{1,80})", re.I), 0.93),
            ("registry_sheet", re.compile(r"\bfolha\s*[:#-]?\s*(?P<value>[A-Z0-9./-]{1,30})", re.I), 0.91),
            ("property_parcel", re.compile(r"\bparcela\s*[:#-]?\s*(?P<value>[A-Z0-9./-]{1,30})", re.I), 0.90),
            ("property_type", re.compile(r"\b(?P<value>pr[eé]dio\s+(?:r[uú]stico|urbano|misto))\b", re.I), 0.96 if cadastral else 0.88),
            ("signed_date", re.compile(r"\b(?:celebrado|assinado|outorgado|feito)(?:\s+em)?\D{0,70}(?P<value>\d{1,2}[/-]\d{1,2}[/-](?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}-\d{2})", re.I), 0.91),
        )
        for field_name, pattern, confidence in patterns:
            for match in pattern.finditer(page.raw_text):
                _append_match_candidate(
                    candidates, seen, field_name, match, page, confidence
                )
        if cadastral:
            _extract_cadastral_owners(page, candidates, seen)
        else:
            _extract_contract_parties(page, candidates, seen)
    return candidates


def apply_step37_resolution(result: ExtractionResult, report: Step37Report) -> ExtractionResult:
    """Publish only evidence-backed focused decisions and attach full audit."""
    by_field: dict[str, list[DeterministicCandidate]] = {}
    for candidate in report.deterministic_candidates:
        if candidate.confidence >= 0.50:
            by_field.setdefault(candidate.field, []).append(candidate)

    field_mapping = {
        "lessor": "lessor",
        "lessor_tax_id": "lessor_tax_id",
        "tenant": "lessee",
        "tenant_tax_id": "lessee_tax_id",
        "contract_representative": "contract_representative",
        "cadastral_owner": "owner_name",
        "cadastral_owner_tax_id": "owner_tax_id",
        "property_article": "property_article",
        "property_section": "property_section",
        "property_parish": "property_parish",
        "property_municipality": "property_municipality",
        "property_district": "property_district",
        "property_type": "property_type",
        "property_location": "property_location",
        "registry_description": "registry_description",
        "registry_number": "registry_number",
        "registry_sheet": "registry_sheet",
        "property_parcel": "property_parcel",
        "signed_date": "signed_date",
    }
    primary = (
        "lessor", "tenant", "cadastral_owner", "property_article",
        "property_section", "property_parish", "property_municipality", "property_type",
    )
    decisions: dict[str, dict[str, object]] = {}
    for field_name, attr_name in field_mapping.items():
        candidates = by_field.get(field_name, [])
        preferred = _preferred_candidates(field_name, candidates)
        selected = preferred[0] if preferred else None
        if field_name in {"lessor", "tenant", "cadastral_owner"} and preferred:
            values = list(dict.fromkeys(item.value for item in preferred))
            value = "; ".join(values)
        else:
            value = selected.value if selected else ""

        existing = str(getattr(result, attr_name, "") or "").strip()
        conflict_values = list(dict.fromkeys(
            [item.value for item in candidates] + ([existing] if existing else [])
        ))
        if selected:
            setattr(result, attr_name, value)
            decisions[field_name] = {
                "value": value,
                "confidence": round(selected.confidence, 3),
                "source_method": "deterministic_confirmed_by_llm"
                if existing and _same_value(existing, value)
                else "deterministic",
                "source_page": selected.source_page,
                "semantic_page": selected.semantic_page,
                "source": selected.source,
                "evidence": selected.evidence_text,
                "validated": True,
                "conflict": len({_comparison_text(item) for item in conflict_values}) > 1,
                "decision_reason": _decision_reason(field_name, selected),
            }
        elif existing and (evidence := _find_model_evidence(existing, field_name, report.pages)):
            decisions[field_name] = {
                "value": existing,
                "confidence": 0.70,
                "source_method": "llm_with_textual_evidence",
                "source_page": evidence.page_number,
                "semantic_page": evidence.semantic_role,
                "source": evidence.source,
                "evidence": _evidence_excerpt(evidence.raw_text, existing),
                "validated": True,
                "conflict": False,
                "decision_reason": "model_value_found_in_selected_page",
            }
        elif field_name in primary:
            # A model value without locatable evidence is not a final Step 3.7
            # fact.  This also prevents filename-only suggestions leaking in.
            if existing:
                setattr(result, attr_name, "")
            decisions[field_name] = {
                "value": None,
                "confidence": 0.0,
                "validated": False,
                "conflict": False,
                "missing_reason": "no_supported_evidence",
            }

    result.property_matrix_key = _matrix_key(
        result.property_article, result.property_section
    )
    contract_articles = list(dict.fromkeys(
        item.value for item in by_field.get("property_article", [])
        if item.semantic_page.startswith("contract_page_")
    ))
    raw = dict(result.raw_json) if isinstance(result.raw_json, dict) else {}
    payload = report.to_dict()
    payload["field_results"] = decisions
    payload["contract_property_articles"] = contract_articles
    article_decision = decisions.get("property_article", {})
    payload["cadastral_confirmed_article"] = (
        article_decision.get("value")
        if article_decision.get("semantic_page") == "cadastral_page_1"
        else None
    )
    raw["step3_7"] = payload
    result.raw_json = raw
    result.deterministic_json = dict(result.deterministic_json or {})
    result.deterministic_json["step3_7_candidates"] = [
        item.to_dict() for item in report.deterministic_candidates
    ]
    result.cadastral_evidence_status = (
        "focused_cadastral_page_selected" if report.cadastral_page else "not_found"
    )
    result.cadastral_evidence_pages = str(report.cadastral_page or "")
    _apply_focused_review_policy(result, decisions)
    return result


def _preferred_text_pdf(pdf_path: Path, config: Any) -> tuple[Path, str]:
    ocr_dir = Path(getattr(config, "ocr_dir", pdf_path.parent))
    cached = ocr_dir / f"{pdf_path.stem}__ocr.pdf"
    if cached.is_file():
        return cached, "cached_ocr_pdf"
    return pdf_path, "native_pdf"


def _plan_visual_confirmation(
    report: Step37Report,
    candidates: list[CadastralPageCandidate],
    config: Any,
) -> None:
    reasons: list[str] = []
    valid = [item for item in candidates if item.is_valid]
    threshold = float(
        getattr(config, "step_3_7_cadastral_ocr_quality_threshold", 0.60)
    )
    if not valid:
        reasons.append("no_candidate_above_threshold")
    if valid:
        best_score = max(item.score for item in valid)
        if sum(item.score == best_score for item in valid) > 1:
            reasons.append("candidate_score_tie")
        chosen = min(valid, key=lambda item: item.page_number)
        if chosen.ocr_quality < threshold:
            reasons.append("best_candidate_ocr_below_threshold")
    limit = int(
        getattr(config, "step_3_7_maximum_cadastral_visual_candidates", 3)
    )
    ranked = sorted(
        (item for item in candidates if item.score > 0 or item.ocr_quality < 0.15),
        key=lambda item: (-item.score, item.page_number),
    )
    report.visual_confirmation_required = bool(reasons and limit > 0)
    report.visual_confirmation_reasons = reasons
    report.visual_candidate_pages = [item.page_number for item in ranked[:limit]] if reasons else []


def _build_context(
    file_name: str,
    total_pages: int,
    pages: list[FocusedPage],
    *,
    deterministic_candidates: list[DeterministicCandidate],
    max_chars_per_page: int,
    max_total_chars: int,
) -> tuple[list[FocusedPage], str, bool]:
    candidate_lines = [
        f"- {item.field}={item.value} | PDF_PAGE={item.source_page} | "
        f"ROLE={item.semantic_page} | CONFIDENCE={item.confidence:.2f} | "
        f"EVIDENCE={item.evidence_text[:180]}"
        for item in deterministic_candidates[:40]
    ]
    candidate_summary = (
        "[DETERMINISTIC_CANDIDATES]\n" + "\n".join(candidate_lines) + "\n\n"
        if candidate_lines
        else "[DETERMINISTIC_CANDIDATES]\nnone\n\n"
    )
    summary_limit = max(240, min(5_000, max_total_chars // 6))
    if len(candidate_summary) > summary_limit:
        candidate_summary = (
            candidate_summary[: max(0, summary_limit - 38)].rstrip()
            + "\n[CANDIDATE_SUMMARY_TRUNCATED=true]\n\n"
        )
    metadata = (
        "[DOCUMENT_METADATA]\n"
        f"filename: {file_name}\n"
        f"total_pages: {total_pages}\n"
        f"selected_pages: {', '.join(str(page.page_number) for page in pages)}\n\n"
    ) + candidate_summary
    available = max(0, max_total_chars - len(metadata))
    if not pages or available <= 0:
        return [], metadata[:max_total_chars], True
    fair_limit = max(1, available // len(pages) - 90)
    per_page_limit = max(1, min(max_chars_per_page, fair_limit))
    rendered_pages: list[FocusedPage] = []
    chunks: list[str] = []
    any_truncated = False
    for page in pages:
        text, truncated = _priority_truncate(page.normalized_text, per_page_limit)
        updated = FocusedPage(
            **{
                **asdict(page),
                "context_text": text,
                "page_context_truncated": truncated,
            }
        )
        rendered_pages.append(updated)
        any_truncated = any_truncated or truncated
        label = page.semantic_role.upper()
        chunks.append(
            f"[{label} | PDF_PAGE={page.page_number} | SOURCE={page.source} | "
            f"OCR_SCORE={page.ocr_score:.2f} | TRUNCATED={'true' if truncated else 'false'}]\n{text}"
        )
    context = metadata + "\n\n".join(chunks)
    # Header overhead can vary with filenames.  A final explicit marker is
    # preferable to a silent overrun, but page text was already priority-cut.
    if len(context) > max_total_chars:
        context = context[: max(0, max_total_chars - 28)].rstrip() + "\n[CONTEXT_TRUNCATED=true]"
        any_truncated = True
    return rendered_pages, context, any_truncated


def _priority_truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    lines = text.splitlines()
    core: set[int] = set()
    for index, line in enumerate(lines):
        if _CORE_KEYWORDS_RE.search(line):
            core.update(range(max(0, index - 1), min(len(lines), index + 2)))
    # Preserve both edges so a signature/date or a late labelled field is not
    # silently sacrificed when the page is unusually dense.
    edges = [
        *range(min(4, len(lines))),
        *range(max(0, len(lines) - 4), len(lines)),
    ]
    chosen: set[int] = set()
    used = 0
    for index in [*sorted(core), *edges]:
        if index in chosen:
            continue
        line = lines[index].strip()
        if not line:
            continue
        if used + len(line) + 1 > limit:
            continue
        chosen.add(index)
        used += len(line) + 1
    if not chosen:
        return text[:limit], True
    selected = [lines[index].strip() for index in sorted(chosen)]
    return "\n".join(selected)[:limit], True


def _extract_contract_parties(
    page: FocusedPage,
    candidates: list[DeterministicCandidate],
    seen: set[tuple[str, str, int]],
) -> None:
    role_patterns = (
        (
            "lessor",
            re.compile(
                r"(?P<value>[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ][^\n,;:]{3,150}?)\s*,?\s*"
                r"(?:(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*\d{9}\s*,?\s*)?"
                r"(?:na\s+qualidade\s+de|adiante|doravante|de\s+ora\s+em\s+diante)[^\n;]{0,70}?"
                r"senhor(?:io|ia)s?\b",
                re.I,
            ),
        ),
        (
            "tenant",
            re.compile(
                r"(?P<value>[A-ZÁÀÂÃÉÈÊÍÌÎÓÒÔÕÚÙÛÇ][^\n,;:]{3,150}?)\s*,?\s*"
                r"(?:(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*\d{9}\s*,?\s*)?"
                r"(?:na\s+qualidade\s+de|adiante|doravante|de\s+ora\s+em\s+diante)[^\n;]{0,70}?"
                r"arrendat[aá]ri[oa]s?\b",
                re.I,
            ),
        ),
        ("lessor", re.compile(r"\bsenhor(?:io|ia)s?\s*[:#-]\s*(?P<value>[^\n;]{3,150})", re.I)),
        ("tenant", re.compile(r"\barrendat[aá]ri[oa]s?\s*[:#-]\s*(?P<value>[^\n;]{3,150})", re.I)),
    )
    for field_name, pattern in role_patterns:
        for match in pattern.finditer(page.raw_text):
            value = _clean_name(match.group("value"))
            if _plausible_name(value):
                _append_value_candidate(
                    candidates,
                    seen,
                    field_name,
                    value,
                    match,
                    page,
                    0.96,
                )
                _extract_nearby_tax_id(
                    page,
                    match,
                    candidates,
                    seen,
                    "lessor_tax_id" if field_name == "lessor" else "tenant_tax_id",
                )
    representative_pattern = re.compile(
        r"\brepresentad[oa]s?\s+por\s+(?P<value>[^\n,;]{3,150})",
        re.I,
    )
    for match in representative_pattern.finditer(page.raw_text):
        value = _clean_name(match.group("value"))
        if _plausible_name(value):
            _append_value_candidate(
                candidates,
                seen,
                "contract_representative",
                value,
                match,
                page,
                0.91,
            )


def _extract_cadastral_owners(
    page: FocusedPage,
    candidates: list[DeterministicCandidate],
    seen: set[tuple[str, str, int]],
) -> None:
    owner_patterns = (
        re.compile(r"\b(?:nome|titular|propriet[aá]ri[oa])\s*[:#-]\s*(?P<value>[^\n;|]{3,150})", re.I),
        re.compile(r"\bidentifica[cç][aã]o\s+dos\s+titulares\b.{0,250}?\bnome\s*[:#-]\s*(?P<value>[^\n;|]{3,150})", re.I | re.S),
    )
    for pattern in owner_patterns:
        for match in pattern.finditer(page.raw_text):
            prefix = page.raw_text[max(0, match.start() - 16):match.start()]
            if "tipo de" in _comparison_text(prefix):
                continue
            value = _clean_name(match.group("value"))
            if _plausible_name(value):
                _append_value_candidate(
                    candidates, seen, "cadastral_owner", value, match, page, 0.97
                )
    tax_pattern = re.compile(
        r"\b(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(?P<value>\d{9})\b",
        re.I,
    )
    for heading in re.finditer(
        r"\b(?:identifica[cç][aã]o\s+dos\s+titulares|titulares)\b",
        page.raw_text,
        re.I,
    ):
        end = min(len(page.raw_text), heading.end() + 900)
        for match in tax_pattern.finditer(page.raw_text, heading.end(), end):
            value = match.group("value")
            if _valid_nif(value):
                _append_value_candidate(
                    candidates,
                    seen,
                    "cadastral_owner_tax_id",
                    value,
                    match,
                    page,
                    0.96,
                )


def _append_match_candidate(
    candidates: list[DeterministicCandidate],
    seen: set[tuple[str, str, int]],
    field_name: str,
    match: re.Match[str],
    page: FocusedPage,
    confidence: float,
) -> None:
    value = match.group("value").strip(" \t.,;:-|“”\"'")
    if field_name == "property_section":
        value = value.upper()
    _append_value_candidate(
        candidates, seen, field_name, value, match, page, confidence
    )


def _extract_nearby_tax_id(
    page: FocusedPage,
    role_match: re.Match[str],
    candidates: list[DeterministicCandidate],
    seen: set[tuple[str, str, int]],
    field_name: str,
) -> None:
    pattern = re.compile(
        r"\b(?:NIF|NIPC|contribuinte)\s*(?:n[.ºo°]?\s*)?[:#-]?\s*(?P<value>\d{9})\b",
        re.I,
    )
    for match in pattern.finditer(page.raw_text, role_match.start(), role_match.end()):
        value = match.group("value")
        if _valid_nif(value):
            _append_value_candidate(
                candidates,
                seen,
                field_name,
                value,
                match,
                page,
                0.94,
            )


def _append_value_candidate(
    candidates: list[DeterministicCandidate],
    seen: set[tuple[str, str, int]],
    field_name: str,
    value: str,
    match: re.Match[str],
    page: FocusedPage,
    confidence: float,
) -> None:
    normalized = _comparison_text(value)
    key = (field_name, normalized, page.page_number)
    if not value or key in seen:
        return
    seen.add(key)
    candidates.append(
        DeterministicCandidate(
            field=field_name,
            value=value,
            normalized_value=normalized,
            source_page=page.page_number,
            semantic_page=page.semantic_role,
            source_method="deterministic",
            source=page.source,
            evidence_text=" ".join(match.group(0).split())[:400],
            start_offset=match.start(),
            end_offset=match.end(),
            confidence=confidence,
        )
    )


def _preferred_candidates(
    field_name: str,
    candidates: list[DeterministicCandidate],
) -> list[DeterministicCandidate]:
    if not candidates:
        return []
    cadastral_fields = {
        "cadastral_owner", "cadastral_owner_tax_id", "property_article", "property_section",
        "property_parish", "property_municipality", "property_district",
        "property_type", "property_location", "registry_description",
        "registry_number", "registry_sheet", "property_parcel",
    }
    preferred_role = "cadastral_page_1" if field_name in cadastral_fields else "contract_page_"
    preferred = [
        item for item in candidates
        if item.semantic_page == preferred_role
        or (preferred_role.endswith("_") and item.semantic_page.startswith(preferred_role))
    ]
    pool = preferred or candidates
    return sorted(pool, key=lambda item: (-item.confidence, item.source_page))


def _find_model_evidence(
    value: str,
    field_name: str,
    pages: Iterable[FocusedPage],
) -> FocusedPage | None:
    wanted = _comparison_text(value)
    if not wanted:
        return None
    cadastral = field_name in {
        "cadastral_owner", "property_article", "property_section", "property_parish",
        "property_municipality", "property_type",
    }
    for page in pages:
        if cadastral and page.semantic_role != "cadastral_page_1":
            continue
        if field_name in {"lessor", "tenant"} and not page.semantic_role.startswith("contract_page_"):
            continue
        if wanted in _comparison_text(page.raw_text):
            return page
    return None


def _decision_reason(field_name: str, selected: DeterministicCandidate) -> str:
    if selected.semantic_page == "cadastral_page_1" and field_name.startswith("property_"):
        return "direct_labeled_cadastral_evidence"
    if field_name == "cadastral_owner":
        return "direct_cadastral_holder_evidence"
    if field_name in {"lessor", "tenant"}:
        return "direct_contract_role_evidence"
    return "direct_labeled_evidence"


def _evidence_excerpt(text: str, value: str) -> str:
    match = re.search(re.escape(value), text, re.I)
    if not match:
        return value
    return " ".join(text[max(0, match.start() - 100): match.end() + 100].split())


def _contains_indicator(text: str, phrase: str) -> bool:
    if phrase in text:
        return True
    if len(phrase) < 8 or not text:
        return False
    # Compare against line-sized windows; this tolerates a small OCR error
    # without treating an isolated generic token as structural evidence.
    window = len(phrase)
    for start in range(0, max(1, len(text) - window + 1), max(1, window // 5)):
        sample = text[start:start + window + 4]
        if SequenceMatcher(None, phrase, sample).ratio() >= 0.86:
            return True
    return False


def _text_quality(text: str) -> float:
    clean = str(text or "")
    if not clean.strip():
        return 0.0
    printable = sum(character.isprintable() for character in clean) / len(clean)
    alnum = sum(character.isalnum() for character in clean) / len(clean)
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}", clean)
    word_factor = min(1.0, len(words) / 35)
    return max(0.0, min(1.0, 0.35 * printable + 0.35 * min(1.0, alnum * 2.2) + 0.30 * word_factor))


def _normalize_layout(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _comparison_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", unaccented).lower().split())


def _clean_name(value: str) -> str:
    value = re.sub(r"^\s*(?:entre|e)\s+", "", value, flags=re.I)
    value = re.split(
        r"\b(?:nif|nipc|contribuinte|residente|com\s+sede|morada|representad[oa]\s+por|tipo\s+de\s+titular)\b",
        value,
        maxsplit=1,
        flags=re.I,
    )[0]
    return " ".join(value.strip(" ,;:-.\n").split())


def _plausible_name(value: str) -> bool:
    normalized = _comparison_text(value)
    if len(normalized) < 4 or normalized in {
        "senhorio", "senhoria", "arrendatario", "arrendataria", "titular",
        "proprietario", "proprietaria", "identificacao dos titulares",
    }:
        return False
    return bool(re.search(r"[A-Za-zÀ-ÖØ-öø-ÿ]", value)) and not bool(
        re.search(r"\b(?:servi[cç]o\s+de\s+finan[cç]as|autoridade\s+tribut[aá]ria)\b", value, re.I)
    )


def _valid_nif(value: str) -> bool:
    if not re.fullmatch(r"\d{9}", value):
        return False
    total = sum(int(value[index]) * (9 - index) for index in range(8))
    digit = 11 - (total % 11)
    if digit >= 10:
        digit = 0
    return digit == int(value[-1])


def _same_value(left: str, right: str) -> bool:
    return _comparison_text(left) == _comparison_text(right)


def _matrix_key(article: str, section: str) -> str:
    article = re.sub(r"\D", "", str(article or ""))
    section = re.sub(r"[^A-Za-z]", "", str(section or "")).upper()
    if not article:
        return ""
    return f"{article}-{section}" if section else article


def _apply_focused_review_policy(
    result: ExtractionResult,
    decisions: dict[str, dict[str, object]],
) -> None:
    """Secondary omissions and resolved conflicts never force Step 3.7 review."""
    secondary_missing = {
        "missing_monthly_rent", "missing_annual_rent", "missing_rent_amount",
        "missing_rent_frequency", "missing_contract_start_date",
        "missing_contract_end_date", "missing_leased_parcel_area",
    }
    populated = {
        "lessor": bool(result.lessor),
        "lessee": bool(result.lessee),
        "signed_date": bool(result.signed_date),
        "property_article": bool(result.property_article),
        "property_section": bool(result.property_section),
        "owner_name": bool(result.owner_name),
    }

    def keep(reason: str) -> bool:
        code = reason.strip()
        if not code or code in secondary_missing:
            return False
        if code.startswith("missing_") and populated.get(code.removeprefix("missing_"), False):
            return False
        if "filename" in code and ("property_article" in code or "property_section" in code):
            return False
        if code == "low_confidence" and all(
            bool(decisions.get(field, {}).get("validated"))
            for field in ("lessor", "tenant", "property_article")
        ):
            return False
        return True

    validation = [
        item.strip() for item in str(result.validation_issues or "").split(";")
        if keep(item)
    ]
    review = [
        item.strip() for item in str(result.review_reason or "").split(";")
        if keep(item)
    ]
    result.validation_issues = "; ".join(dict.fromkeys(validation))
    result.review_reason = "; ".join(dict.fromkeys(review))
    primary_missing = [
        field for field in ("lessor", "tenant", "property_article")
        if not bool(decisions.get(field, {}).get("validated"))
    ]
    if not review and not validation and not primary_missing and not result.error_message:
        result.needs_review = "no"
        result.human_review_required = "no"
        result.validation_status = "AUTO_APPROVED"
    elif primary_missing:
        result.needs_review = "yes"
        result.validation_status = "NEEDS_REVIEW"
