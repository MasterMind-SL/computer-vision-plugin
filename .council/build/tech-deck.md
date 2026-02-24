# Tech Deck: CV MCP Plugin — Definitive Tool Upgrade

## 1. Technology Stack

| Layer | Technology | Justification |
|-------|-----------|---------------|
| MCP Framework | `mcp>=1.26.0` (FastMCP) | Standard Claude Code plugin protocol, stdio transport |
| OCR Primary | `winocr` | Windows.Media.Ocr via WinRT; GPU-accelerated, ships with OS |
| OCR Fallback | `pytesseract` (optional) | For edge cases where WinRT unavailable |
| Image Processing | `Pillow` | Preprocessing pipeline (upscale, grayscale, sharpen, contrast) |
| UI Automation | `comtypes` + UIAutomationCore | Direct COM access to Windows UIA |
| Fuzzy Matching | `difflib.SequenceMatcher` (stdlib) | Zero new dependencies, sufficient for <500 elements |
| Concurrency | `concurrent.futures.ThreadPoolExecutor` (stdlib) | Parallel UIA + OCR in cv_find |
| Models | `pydantic>=2.0.0` | Strict type validation, BaseModel for all schemas |
| **Zero new dependencies** | All features use existing deps | Pillow, winocr, comtypes, pywin32, difflib, concurrent.futures |

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                       Claude Code                             │
│                (stdio JSON-RPC over MCP)                      │
└──────────────────┬───────────────────────────────────────────┘
                   │ stdin/stdout
┌──────────────────▼───────────────────────────────────────────┐
│              FastMCP Server (server.py)                        │
│     Auto-discovery: @mcp.tool() from src/tools/*.py           │
├──────────────────────────────────────────────────────────────┤
│                    SECURITY GATE                              │
│  security.py: validate_hwnd + check_restricted + log_action   │
│  (+ rate_limit + dry_run for mutating tools only)             │
├──────────┬──────────┬──────────┬─────────┬──────────────────┤
│ tools/   │ tools/   │ tools/   │ tools/  │ tools/ (NEW)     │
│windows.py│capture.py│input_*.py│ ocr.py  │find.py           │
│          │          │          │(MODIFY) │text_extract.py   │
├──────────┴──────────┴──────────┴─────────┴──────────────────┤
│                  UTILITY LAYER (utils/)                        │
│  screenshot.py | win32_input.py | win32_window.py             │
│  security.py   | uia.py        | ocr_engine.py (NEW)         │
├──────────────────────────────────────────────────────────────┤
│               CROSS-CUTTING (src/ root)                       │
│  dpi.py | coordinates.py | errors.py | models.py | config.py │
├──────────────────────────────────────────────────────────────┤
│              Win32 API / mss / winocr / comtypes              │
└──────────────────────────────────────────────────────────────┘
```

## 3. Component Design

### 3a. NEW: OCR Engine (`src/utils/ocr_engine.py`)

Core OCR logic extracted from `src/tools/ocr.py` into a reusable utility class.

**`OcrEngine` class**:
- `__init__()`: Detects and caches installed OCR languages via `winocr.list_available_languages()`. Builds ordered preference list: `en-US` > `en-*` > other installed.
- `recognize(image: Image, lang: str | None, preprocess: bool, origin: Point | None) -> OcrResult`: Main OCR pipeline.
- `preprocess_image(image: Image) -> Image`: Sequential pipeline: (1) upscale 2x if height < 300px via LANCZOS, (2) convert to grayscale `"L"`, (3) sharpen via `ImageFilter.SHARPEN`, (4) auto-contrast via `ImageOps.autocontrast`.
- `_extract_regions_winocr(result, origin) -> list[OcrRegion]`: Iterates `line.words`, extracts `word.bounding_rect` (x, y, width, height), computes line-level bbox as union, translates coords using origin offset.
- `_extract_regions_pytesseract(data, origin) -> list[OcrRegion]`: Uses `image_to_data(output_type=Output.DICT)` for `left`, `top`, `width`, `height`, `conf` fields.

**Language caching**: Called once at first use (lazy init), cached as `_installed_langs: list[str]`. Thread-safe via module-level singleton.

**Origin offset**: When `origin` is provided, all bbox coordinates are translated: `bbox.x += origin.x`, `bbox.y += origin.y`. This converts image-relative to screen-absolute.

### 3b. MODIFIED: OCR Tool (`src/tools/ocr.py`)

**New parameters**:
- `lang: str | None = None` — Force specific OCR language
- `preprocess: bool = True` — Enable/disable image preprocessing

**Changes**:
- Delegate all OCR logic to `OcrEngine` singleton from `src/utils/ocr_engine.py`
- Compute origin from `GetWindowRect(hwnd)` or `(x0, y0)` for region
- Return enhanced schema: existing `text`, `regions`, `engine` fields PLUS `words` (word-level), `confidence` (float), `language` (str), `origin` (Point)
- Use `OcrRegion` Pydantic model (not plain dicts) for validation
- **Security fix**: Add `validate_hwnd_fresh` + `check_restricted` + `log_action` gates when `hwnd` is provided (currently has NONE)
- Apply redaction patterns to all output text

**Backward compatibility**: All existing fields preserved. New fields are additive only.

### 3c. NEW: Find Tool (`src/tools/find.py`)

**`cv_find(query: str, hwnd: int, method: str = "auto", max_results: int = 20)`**

Three-tier matching pipeline:

**Tier 1 — UIA match** (~1-2s):
- Call `get_ui_tree(hwnd, depth=8, filter="all")`
- Flatten tree to list of `(name, control_type, rect, ref_id)`
- Fuzzy-match `query` against element `name` using `SequenceMatcher.ratio()` with threshold 0.5
- Also match against `control_type` for queries like "button", "edit", "checkbox"
- Sort by match score descending

**Tier 2 — OCR fallback** (~3-5s):
- If UIA yields 0 results, capture window via `capture_window_raw(hwnd)`
- Run `OcrEngine.recognize()` with preprocessing
- Fuzzy-match `query` against OCR region text
- Translate bboxes to screen-absolute using window origin

**Tier 3 — Auto mode** (default):
- Run UIA first. If 0 results, run OCR.
- If both produce results, merge and deduplicate by bbox overlap (IoU > 0.5 → keep UIA result)
- Use `ThreadPoolExecutor` to run UIA and OCR concurrently when `method="auto"` for latency optimization

**Validation**: Before returning, validate ALL bboxes fall within target window rect from `GetWindowRect`. Reject bboxes outside window bounds (coordinate mapping error protection).

**Security**: `validate_hwnd_fresh` + `check_restricted` + `log_action`. Cap `query` to 500 chars. Use `difflib` not regex (ReDoS-safe).

### 3d. NEW: Text Extract Tool (`src/tools/text_extract.py`)

**`cv_get_text(hwnd: int, method: str = "auto")`**

**UIA text extraction** (primary, ~1-2s):
- Walk UIA tree via `get_ui_tree(hwnd, depth=10, filter="all")`
- Collect all elements with non-empty `name` and `control_type` in `{Text, Edit, Document, ListItem, DataItem}`
- **Password detection**: Check `IsPassword` UIA property. Redact values from password fields as `[PASSWORD]`
- Spatial sorting: sort by `(rect.y // 20, rect.x)` — groups elements into rows by y-proximity, then left-to-right within each row
- Join with newlines; insert double newline for large y-gaps (paragraph breaks)

**OCR fallback** (~3-5s):
- Triggers when UIA text < 20 chars
- Capture window, run `OcrEngine.recognize()` with preprocessing
- Sort OCR regions spatially same as above

**Redaction**: Apply `CV_OCR_REDACTION_PATTERNS` to ALL output (both UIA and OCR paths). PII patterns applied regardless of source.

**Security**: `validate_hwnd_fresh` + `check_restricted` + `log_action`.

## 4. Data Models (`src/models.py` — MODIFIED)

### New Models

```python
class OcrWord(BaseModel):
    """A single OCR-detected word with bounding box."""
    text: str
    bbox: Rect
    confidence: float = 0.0

class FindMatch(BaseModel):
    """A single match from cv_find."""
    text: str
    bbox: Rect
    confidence: float
    source: str  # "uia" or "ocr"
    ref_id: str
    control_type: str | None = None
```

### Enhanced Models

```python
class OcrRegion(BaseModel):  # ENHANCED
    text: str
    bbox: Rect                              # NOW POPULATED (was always empty)
    confidence: float = 0.0                 # NOW POPULATED
    words: list[OcrWord] = Field(default_factory=list)  # NEW: word-level detail
```

### HWND Validation (all tools)

```python
def validate_hwnd(hwnd: int) -> int:
    """Validate HWND is within valid Win32 range."""
    if not (0 < hwnd <= 0xFFFFFFFF):
        raise ValueError(f"Invalid HWND: {hwnd}")
    return hwnd
```

## 5. API Contracts

### cv_ocr (Enhanced)
```json
{
  "success": true,
  "text": "Google Search  I'm Feeling Lucky",
  "regions": [
    {
      "text": "Google Search",
      "bbox": {"x": 550, "y": 420, "width": 120, "height": 30},
      "confidence": 0.96,
      "words": [
        {"text": "Google", "bbox": {"x": 550, "y": 420, "width": 60, "height": 30}, "confidence": 0.98},
        {"text": "Search", "bbox": {"x": 615, "y": 420, "width": 55, "height": 30}, "confidence": 0.95}
      ]
    }
  ],
  "engine": "winocr",
  "language": "en-US",
  "origin": {"x": 0, "y": 0},
  "confidence": 0.96
}
```

### cv_find (New)
```json
{
  "success": true,
  "matches": [
    {
      "text": "Google Search",
      "bbox": {"x": 550, "y": 420, "width": 120, "height": 30},
      "confidence": 0.92,
      "source": "ocr",
      "ref_id": "ref_0",
      "control_type": null
    }
  ],
  "match_count": 1,
  "method_used": "ocr"
}
```

### cv_get_text (New)
```json
{
  "success": true,
  "text": "Google\nGoogle Search  I'm Feeling Lucky\nGoogle offered in: Español (Latinoamérica)\nChile\nAbout  Advertising  Business  How Search works",
  "source": "ocr",
  "line_count": 5,
  "confidence": 0.94
}
```

## 6. Security Architecture

### Security Gate by Tool Type

| Tool Type | validate_hwnd | check_restricted | check_rate_limit | guard_dry_run | log_action |
|-----------|:---:|:---:|:---:|:---:|:---:|
| Mutating (click, type, keys, focus, move) | Y | Y | Y | Y | Y |
| Read-only (cv_ocr, cv_find, cv_get_text, cv_read_ui) | Y | Y | N | N | Y |
| Passive (list_windows, list_monitors, wait) | N | N | N | N | N |

### New Security Measures

1. **cv_ocr now has security gates** — Currently missing. Add validate_hwnd + check_restricted + log_action when hwnd provided.
2. **HWND range validation** — All tools validate `0 < hwnd <= 0xFFFFFFFF` before Win32 calls.
3. **cv_find query cap** — Max 500 chars, uses `difflib` (not regex) to prevent ReDoS.
4. **OCR region bounds** — Validate `x1 > x0`, `y1 > y0`, within virtual desktop, cap at 50 megapixels.
5. **cv_find bbox validation** — All returned bboxes must fall within target window rect.
6. **Password field detection** — `cv_get_text` UIA path checks `IsPassword` property, redacts as `[PASSWORD]`.
7. **Universal redaction** — `CV_OCR_REDACTION_PATTERNS` applied to ALL text output (UIA and OCR paths).
8. **Default PII patterns** — Ship with SSN (`\b\d{3}-\d{2}-\d{4}\b`), credit card (`\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b`) patterns.

## 7. Testing Strategy

### New Test Files

```
tests/unit/
├── test_ocr_engine.py       # OcrEngine: bbox extraction, preprocessing, lang cache, origin offset
├── test_ocr_bbox.py         # Specific bbox fix validation with mock winocr results
├── test_find.py             # cv_find: UIA matching, OCR matching, dedup, bbox validation
├── test_text_extract.py     # cv_get_text: UIA text collection, spatial sorting, OCR fallback
├── test_fuzzy_match.py      # difflib matching edge cases (empty, special chars, Unicode)
└── test_security_gates.py   # Security gate enforcement on new and modified tools
```

### Key Test Scenarios

- **OCR bbox**: Mock winocr result with known word bounding_rects → verify line-level union bbox is correct
- **Coordinate translation**: Known window at (500, 300) → OCR bbox (50, 20) → screen-absolute (550, 320)
- **Language caching**: Mock `list_available_languages()` → verify `en-US` preferred over `es-MX`
- **Preprocessing**: Feed low-contrast image → verify grayscale + sharpen + contrast applied
- **cv_find UIA**: Mock UIA tree with "Submit" button → `cv_find("submit")` → match found
- **cv_find OCR fallback**: Mock empty UIA tree + OCR regions → verify OCR fallback triggered
- **cv_find bbox validation**: Return bbox outside window → verify rejected
- **cv_get_text spatial sort**: Elements at various y/x positions → verify top-to-bottom, left-to-right order
- **Password redaction**: UIA element with `IsPassword=True` → verify value redacted
- **PII redaction**: OCR text containing SSN pattern → verify redacted

## 8. File/Directory Structure (Changes Only)

```
src/
  models.py              # ADD: OcrWord, FindMatch. ENHANCE: OcrRegion with words field
  config.py              # ADD: default PII redaction patterns
  errors.py              # ADD: FIND_NO_MATCH, OCR_LOW_CONFIDENCE error codes
  utils/
    ocr_engine.py        # NEW: OcrEngine class (preprocessing, lang cache, bbox extraction)
    security.py          # MODIFY: add HWND range validation helper
    uia.py               # MODIFY: expose IsPassword property on elements
  tools/
    ocr.py               # MODIFY: add lang/preprocess params, delegate to OcrEngine, add security gates
    find.py              # NEW: cv_find tool (UIA + OCR fuzzy search)
    text_extract.py      # NEW: cv_get_text tool (UIA spatial + OCR fallback)
tests/unit/
    test_ocr_engine.py   # NEW
    test_ocr_bbox.py     # NEW
    test_find.py         # NEW
    test_text_extract.py # NEW
    test_fuzzy_match.py  # NEW
    test_security_gates.py # NEW
```

## 9. Integration Points

- `src/tools/find.py` → imports `get_ui_tree` from `src/utils/uia.py` + `OcrEngine` from `src/utils/ocr_engine.py`
- `src/tools/text_extract.py` → imports `get_ui_tree` from `src/utils/uia.py` + `OcrEngine` from `src/utils/ocr_engine.py`
- `src/tools/ocr.py` → delegates to `OcrEngine` instead of inline `_ocr_winocr`/`_ocr_pytesseract`
- All new tools import `mcp` from `src.server` — auto-discovered by `pkgutil` iteration
- Security gates from `src/utils/security.py` — same pattern as `cv_read_ui`
- `capture_window_raw` from `src/utils/screenshot.py` feeds `OcrEngine` (no file round-trip)
- Window rect from `win32gui.GetWindowRect` provides origin offset for coordinate translation
