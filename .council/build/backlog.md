# Implementation Backlog: CV MCP Plugin — Definitive Tool Upgrade

## Foundation Tasks (Team Lead — must complete before parallel work)

### F-1. Update `src/models.py` — Add new models, enhance OcrRegion [M]
- Add `OcrWord(BaseModel)`: `text: str`, `bbox: Rect`, `confidence: float = 0.0`
- Add `FindMatch(BaseModel)`: `text: str`, `bbox: Rect`, `confidence: float`, `source: str`, `ref_id: str`, `control_type: str | None = None`
- Enhance `OcrRegion`: add `words: list[OcrWord] = Field(default_factory=list)` field, add `confidence: float = 0.0` default
- Add `validate_hwnd(hwnd: int) -> int` helper: validate `0 < hwnd <= 0xFFFFFFFF`
- **No breaking changes** — all additions are additive

### F-2. Update `src/errors.py` — Add new error codes [S]
- Add `FIND_NO_MATCH = "FIND_NO_MATCH"` — informational code for cv_find with 0 results
- Add `OCR_LOW_CONFIDENCE = "OCR_LOW_CONFIDENCE"` — when average confidence < 0.7

### F-3. Update `src/config.py` — Add PII redaction patterns [S]
- Add `CV_OCR_REDACTION_PATTERNS` default: SSN pattern (`\b\d{3}-\d{2}-\d{4}\b`), credit card pattern (`\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b`)
- Loaded from env var with sensible defaults, same pattern as existing config vars

### F-4. Update `src/utils/security.py` — HWND validation + redaction update [M]
- Add `validate_hwnd_range(hwnd: int)` that checks `0 < hwnd <= 0xFFFFFFFF` before any Win32 call
- Update `redact_ocr_output()` to accept `list[OcrRegion]` (Pydantic models) in addition to `list[dict]` — must handle both for backward compatibility during transition
- Apply `CV_OCR_REDACTION_PATTERNS` from config to both `text` and `regions[].text` and `regions[].words[].text`

### F-5. Update `src/utils/uia.py` — Expose IsPassword and Value properties [M]
- When walking UIA tree elements, read `UIA_IsPasswordPropertyId` and include `is_password: bool` in element data
- For Edit/Document control types, attempt to read `CurrentValue` via the Value pattern (`IUIAutomationValuePattern`). Include `value: str | None` in element data
- If `IsPassword` is True, redact the value as `[PASSWORD]` in the returned element
- These are required by cv_get_text (Workstream C) for accurate text extraction and password protection

**SYNC POINT SP-0:** All workstreams begin after Foundation tasks complete. dev-alpha can start immediately after F-1 and F-4 are done (does not need F-5).

---

## Workstream A: OcrEngine + OCR Tool Refactor (dev-alpha)
**Features: F1 (Fix OCR Bounding Boxes) + F2 (Improve OCR Accuracy)**
**Dependencies: F-1 (models), F-3 (config), F-4 (security)**

### A-1. Create `src/utils/ocr_engine.py` — OcrEngine class [L]
- **OcrEngine class** (module-level singleton, lazy init):
  - `__init__()`: Detect installed OCR languages via `winocr.list_available_languages()`, cache as `_installed_langs`. Build preference order: `en-US` > `en-*` > other installed languages.
  - `recognize(image: PIL.Image, lang: str | None = None, preprocess: bool = True, origin: Point | None = None) -> dict`: Main pipeline — preprocess → OCR → extract regions → translate coordinates → return structured result.
  - `preprocess_image(image: PIL.Image) -> PIL.Image`: Pipeline: (1) upscale 2x via LANCZOS if height < 300px, (2) convert to grayscale "L", (3) `ImageFilter.SHARPEN`, (4) `ImageOps.autocontrast`.
  - `_select_language(lang: str | None) -> str`: If `lang` provided, validate against installed. If None, use cached preference order.
  - `_extract_regions_winocr(result, origin) -> list[OcrRegion]`: Iterate `line.words`, extract `word.bounding_rect` (x, y, width, height), compute line bbox as union of word bboxes, build `OcrWord` objects, translate coords with origin offset.
  - `_extract_regions_pytesseract(data, origin) -> list[OcrRegion]`: Use `image_to_data(output_type=Output.DICT)` for word-level bboxes and confidence.
- **Origin offset**: When `origin` provided, all bbox coords translated: `x += origin.x`, `y += origin.y`
- **Confidence**: Per-word from winocr word objects, per-line as average of word confidences, overall as average of line confidences
- **Critical**: This is a NEW utility file — does NOT modify `src/tools/ocr.py` yet. Must be independently testable.

### A-2. Refactor `src/tools/ocr.py` — Delegate to OcrEngine [L]
- Replace inline `_ocr_winocr()` and `_ocr_pytesseract()` with calls to `OcrEngine.recognize()`
- Add new parameters: `lang: str | None = None`, `preprocess: bool = True`
- Compute `origin` from `win32gui.GetWindowRect(hwnd)` when hwnd provided, or `Point(x=x0, y=y0)` for region capture
- For hwnd capture: use `capture_window_raw(hwnd)` at native resolution (no max_width downscaling) for OCR clarity
- Return enhanced schema: existing `text`, `regions`, `engine` fields PLUS new `confidence` (float), `language` (str), `origin` (Point dict)
- `regions` now populated with real `OcrRegion` Pydantic models (bbox, confidence, words all filled)
- **Backward compatibility**: ALL existing fields preserved. New fields additive only.
- Apply `redact_ocr_output()` from security.py to all output before returning

### A-3. Add security gates to `cv_ocr` [M]
- **Critical security fix**: cv_ocr currently has ZERO security gates
- When `hwnd` is provided: call `validate_hwnd_fresh(hwnd)` + `check_restricted(process_name)` + `log_action("cv_ocr", params, status)`
- Read-only tool: does NOT need `check_rate_limit` or `guard_dry_run`
- Same pattern as `cv_read_ui` in `src/tools/accessibility.py`

### A-4. Create `tests/unit/test_ocr_engine.py` [M]
- Test language caching: mock `list_available_languages()` → verify `en-US` preferred over `es-MX`
- Test preprocessing pipeline: feed image → verify grayscale + sharpen + contrast applied, verify 2x upscale for small images
- Test bbox extraction from winocr: mock winocr result with known `word.bounding_rect` values → verify line-level union bbox is correct
- Test origin offset: known window at (500, 300), OCR bbox (50, 20) → verify screen-absolute (550, 320)
- Test pytesseract bbox extraction: mock `image_to_data` → verify OcrRegion populated correctly
- Test confidence aggregation: word confidences → line average → overall average

### A-5. Create `tests/unit/test_ocr_bbox.py` [M]
- Specific regression tests for the bbox bug fix
- Mock winocr result with realistic word bounding_rects → verify every region has non-empty bbox
- Test edge cases: single-word line, empty line, very long line
- Test that OcrRegion Pydantic model rejects empty bbox (validation)
- Test coordinate translation with various window positions

**SYNC POINT SP-1:** After A-1 completes, notify dev-beta and dev-gamma that OcrEngine is available. They can begin their workstreams.

---

## Workstream B: cv_find — Natural Language Element Finder (dev-beta)
**Feature: F3 (cv_find)**
**Dependencies: F-1 (models), F-4 (security), A-1 (OcrEngine)**
**BLOCKED BY SP-1** (needs OcrEngine from Workstream A)

### B-1. Create `src/tools/find.py` — cv_find tool [L]
- **Signature**: `cv_find(query: str, hwnd: int, method: str = "auto", max_results: int = 20)`
- **Security gates**: `validate_hwnd_fresh` + `check_restricted` + `log_action` (read-only, no rate limit)
- **Input validation**: Cap `query` to 500 chars. Validate `max_results` in 1-50 range.
- **UIA matching** (Tier 1, ~1-2s):
  - Call `get_ui_tree(hwnd, depth=8, filter="all")`
  - Flatten tree to list of `(name, control_type, rect, ref_id)`
  - Fuzzy-match `query` against element `name` using `difflib.SequenceMatcher.ratio()` with threshold 0.5
  - Also match against `control_type` for queries like "button", "edit", "checkbox"
  - Also match against element `value` if available (from F-5 UIA enhancement)
  - Sort by match score descending
- **OCR matching** (Tier 2, ~3-5s):
  - Capture window via `capture_window_raw(hwnd)` — no file round-trip
  - Run `OcrEngine.recognize()` with preprocessing enabled
  - Fuzzy-match `query` against OCR region text
  - Bboxes already translated to screen-absolute by OcrEngine (via origin offset)
- **Auto mode** (default, Tier 3):
  - Run UIA first. If 0 results, run OCR. **SEQUENTIAL, NOT PARALLEL** (avoids ThreadPoolExecutor nesting with winocr's internal asyncio.run + uia.py threading)
  - If both produce results, merge and deduplicate by bbox overlap (IoU > 0.5 → keep UIA result, higher reliability)
- **Bbox validation**: Before returning, validate ALL bboxes fall within target `GetWindowRect(hwnd)`. Reject bboxes outside window bounds.
- **Return format**: `{success, matches: [FindMatch], match_count, method_used}`
- If 0 matches: return `{success: true, matches: [], match_count: 0}` (not an error)

### B-2. Create `tests/unit/test_find.py` [M]
- Test UIA matching: mock UIA tree with "Submit" button → `cv_find("submit")` → match found with source="uia"
- Test OCR fallback: mock empty UIA tree + OCR regions → verify OCR triggered, source="ocr"
- Test auto mode: mock UIA with results → verify OCR NOT called
- Test deduplication: overlapping UIA and OCR bboxes → verify UIA kept
- Test bbox validation: return bbox outside window rect → verify rejected
- Test max_results cap: 30 matches → verify only top 20 returned
- Test query cap: 600-char query → verify truncated to 500
- Test security gates: verify validate_hwnd + check_restricted + log_action called

### B-3. Create `tests/unit/test_fuzzy_match.py` [S]
- Test SequenceMatcher edge cases: empty query, single char, Unicode (CJK, emoji), special chars
- Test threshold boundary: ratio 0.49 → no match, ratio 0.51 → match
- Test control_type matching: query "button" matches Button control type
- Test case insensitivity in matching
- Test substring matching: query "Sub" matches "Submit"

---

## Workstream C: cv_get_text — Clean Text Extraction (dev-gamma)
**Feature: F4 (cv_get_text)**
**Dependencies: F-1 (models), F-4 (security), F-5 (UIA enhancements), A-1 (OcrEngine)**
**BLOCKED BY SP-0 (Foundation) + SP-1 (OcrEngine)**

### C-1. Create `src/tools/text_extract.py` — cv_get_text tool [L]
- **Signature**: `cv_get_text(hwnd: int, method: str = "auto")`
- **Security gates**: `validate_hwnd_fresh` + `check_restricted` + `log_action` (read-only)
- **UIA text extraction** (primary, ~1-2s):
  - Call `get_ui_tree(hwnd, depth=10, filter="all")`
  - Collect elements with non-empty `name` and `control_type` in `{Text, Edit, Document, ListItem, DataItem}`
  - For Edit/Document types: also collect `value` property (from F-5 UIA enhancement)
  - **Password detection**: Check `is_password` property from UIA. Redact value as `[PASSWORD]`
  - **Spatial sorting**: Sort by `(rect.y // 20, rect.x)` — groups into rows by y-proximity, left-to-right within rows
  - Join with newlines; insert double newline for large y-gaps (> 40px) as paragraph breaks
- **OCR fallback** (~3-5s):
  - Triggers when UIA text < 20 chars (common for Chrome/Electron)
  - Capture window via `capture_window_raw(hwnd)`, run `OcrEngine.recognize()` with preprocessing
  - Sort OCR regions spatially same as UIA path
- **Redaction**: Apply `CV_OCR_REDACTION_PATTERNS` to ALL output text (both UIA and OCR paths)
- **Return format**: `{success, text, source, line_count, confidence}`
  - `source`: "uia", "ocr", or "hybrid"
  - `confidence`: 1.0 for UIA, average word confidence for OCR

### C-2. Create `tests/unit/test_text_extract.py` [M]
- Test UIA text extraction: mock UIA tree with Text/Edit elements → verify text collected
- Test spatial sorting: elements at various y/x positions → verify top-to-bottom, left-to-right order
- Test paragraph breaks: elements with large y-gap → verify double newline inserted
- Test password redaction: UIA element with `is_password=True` → verify value redacted as `[PASSWORD]`
- Test OCR fallback: mock UIA returning < 20 chars → verify OCR triggered
- Test PII redaction: text containing SSN pattern → verify redacted
- Test security gates: verify validate_hwnd + check_restricted + log_action called

---

## Cross-Workstream Sync Points

| Point | Trigger | Unblocks |
|-------|---------|----------|
| **SP-0** | Foundation tasks F-1 through F-5 complete | Workstream A begins; C can begin UIA-dependent prep |
| **SP-1** | Workstream A task A-1 complete (OcrEngine ready) | Workstream B (cv_find) + Workstream C (cv_get_text OCR fallback) |
| **SP-2** | All workstream feature tasks complete | Post-integration testing |

---

## Post-Integration Tasks (Team Lead — after all workstreams complete)

### P-1. Create `tests/unit/test_security_gates.py` [M]
- Verify cv_ocr now has security gates (was missing before upgrade)
- Verify cv_find has security gates (validate_hwnd + check_restricted + log_action)
- Verify cv_get_text has security gates
- Verify HWND range validation on all tools
- Verify PII redaction applied to all text output paths

### P-2. Integration verification [M]
- Run full test suite: `uv run pytest tests/unit/ -v`
- Verify server starts and all 16 tools registered (14 existing + cv_find + cv_get_text)
- Verify cv_ocr backward compatibility: existing fields unchanged, new fields additive
- Verify OcrEngine singleton works across multiple tool calls
- Fix any import errors or integration issues

---

## Complexity Summary

| Size | Count | Tasks |
|------|-------|-------|
| Small (S) | 3 | F-2, F-3, B-3 |
| Medium (M) | 9 | F-1, F-4, F-5, A-3, A-4, A-5, B-2, C-2, P-1, P-2 |
| Large (L) | 4 | A-1, A-2, B-1, C-1 |

## Feature Coverage Verification

| PRD Feature | Workstream | Task(s) |
|-------------|-----------|---------|
| F1: Fix OCR Bounding Boxes | A | A-1 (OcrEngine bbox extraction), A-2 (OCR tool refactor), A-5 (bbox tests) |
| F2: Improve OCR Accuracy | A | A-1 (lang cache, preprocessing, confidence), A-2 (lang/preprocess params), A-4 (engine tests) |
| F3: cv_find Element Finder | B | B-1 (find tool), B-2 (find tests), B-3 (fuzzy match tests) |
| F4: cv_get_text Extraction | C | C-1 (text extract tool), C-2 (text extract tests) |
| Security: cv_ocr gates | A | A-3 (security gates on cv_ocr) |
| Security: HWND validation | Foundation | F-4 (HWND range validation) |
| Security: PII redaction | Foundation | F-3 (config patterns), F-4 (redaction update) |
| Security: Password detection | Foundation + C | F-5 (UIA IsPassword), C-1 (redaction in cv_get_text) |
| Security: All new tools gated | Post-Integration | P-1 (security gate verification) |

**All 4 PRD features + all security requirements assigned. Zero deferrals.**
