# PRD: Computer Vision MCP Plugin — Definitive Tool Upgrade

## 1. Problem Statement

The Computer Vision MCP plugin is the only automation tool that works across ALL Windows applications (Excel, PowerPoint, native apps, legacy software). However, benchmarking against Chrome's Claude-in-Chrome MCP revealed four critical gaps that prevent it from being the definitive automation tool:

1. **OCR bounding boxes are always empty** — `_ocr_winocr()` checks `line.x` and `line.bbox`, but winocr's `OcrLine` objects expose bounding rectangles through `line.words[i].bounding_rect`, not directly on lines. Every region returns `bbox: {}`, making it impossible to click on detected text.
2. **OCR accuracy is ~90%** — Spanish locale interference causes `es-MX` to win the language probe before `en`, producing errors like "A1" for "AI" and "ñ" for "h". No image preprocessing, no confidence scoring, no language override.
3. **No natural language element finder** — Chrome MCP has `find("search button")` returning clickable refs. Our plugin requires a 4-step workflow: screenshot → OCR → parse coordinates → click.
4. **No clean text extraction** — Chrome MCP has `get_page_text` with 100% DOM accuracy. Our only text path is OCR, which is lossy and error-prone. UIA can provide perfect text for native apps but is unexposed as a user-facing tool.

## 2. Target Users

- **Claude Code agents** automating Windows desktop workflows (Excel data entry, PowerPoint editing, native app testing, ERP systems)
- **Enterprise automation builders** using Claude to drive legacy Windows applications without APIs
- **Cross-platform orchestrators** who need a single tool for native Windows apps (Chrome MCP cannot reach these)

## 3. Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| OCR bbox population rate | 0% (always empty) | 100% of detected words/lines have valid bbox |
| OCR character accuracy (English text) | ~90% | 97%+ regardless of Windows locale |
| Tool calls to click a described element | 4 (screenshot, OCR, parse, click) | 1 (`cv_find`) |
| Text extraction accuracy (UIA apps) | ~90% (OCR only) | 99%+ (UIA primary path) |
| New tool count | 14 | 16 (+`cv_find`, +`cv_get_text`) |

## 4. Core Features (All Mandatory)

### Feature 1: Fix OCR Bounding Boxes

**Root cause**: winocr returns `OcrResult` with `lines[]`, each containing `words[]`. Bounding box data is on word objects (`word.bounding_rect` → x, y, width, height), NOT on line objects. Current code checks `hasattr(line, "x")` and `hasattr(line, "bbox")` — both fail, so `bbox_dict` stays `{}`.

**Implementation**:
- Iterate `line.words`, extract each word's `bounding_rect` (x, y, width, height)
- Compute line-level bbox as the union (enclosing rectangle) of all word bounding rects
- Return BOTH line-level regions and word-level regions (new `words` field on each region)
- Use the existing `OcrRegion` Pydantic model (currently unused — code constructs plain dicts). Force validation: `Rect` requires all 4 int fields, so empty bboxes will fail at model level
- Include confidence scores from winocr word objects where available
- **Coordinate origin**: OCR bboxes are image-relative. Include an `origin` field `{x, y}` in screen-absolute coordinates (from `GetWindowRect` for hwnd, or `(x0, y0)` for region). The `cv_find` tool must translate bboxes to screen-absolute before returning.

**Pytesseract path**: Also fix pytesseract fallback — use `image_to_data(output_type=Output.DICT)` which returns `left`, `top`, `width`, `height`, and `conf` fields. Populate `OcrRegion` with these.

### Feature 2: Improve OCR Accuracy to 97%+

Three changes, ordered by impact:

1. **Language detection fix**:
   - Detect installed OCR language packs once at startup via `Windows.Media.Ocr.OcrEngine.AvailableRecognizerLanguages`, cache the list
   - Prefer `en-US`/`en` when installed, regardless of system locale
   - Add a `lang` parameter to `cv_ocr` so callers can force a specific language when known, bypassing auto-detection
   - Only fall back to other languages when English is genuinely not installed

2. **Image preprocessing pipeline** (optional, `preprocess=True` default):
   - Upscale small images to 2x if height < 300px (improves glyph clarity)
   - Convert to grayscale
   - Apply sharpening (Pillow `ImageFilter.SHARPEN` or `UnsharpMask`)
   - Increase contrast (Pillow `ImageEnhance.Contrast`)
   - All using existing Pillow dependency — zero new deps

3. **Confidence scoring**:
   - Extract per-word confidence from winocr word objects
   - Aggregate to per-line confidence (average of word confidences)
   - Include in `OcrRegion` output
   - For pytesseract: use existing `conf` field from `image_to_data`

4. **DPI-aware capture**: When OCR-ing from hwnd, capture at native resolution (no downscaling via `max_width`) for maximum glyph clarity.

### Feature 3: `cv_find` — Natural Language Element Finder

**Signature**: `cv_find(query: str, hwnd: int | None = None, x0/y0/x1/y1: int | None = None) -> dict`

**Three-tier matching pipeline**:
1. **UIA match** (fastest): Query UIA tree via existing `get_ui_tree()`. Fuzzy-match `query` against element `Name`, `ControlType`, and `Value` using `difflib.SequenceMatcher` + substring matching. No external NLP dependency.
2. **OCR fallback**: If UIA returns 0 results (common for Chrome, Electron apps), capture window screenshot, run improved OCR (Feature 2), fuzzy-match `query` against OCR text regions. Depends on Feature 1 bboxes.
3. **Auto mode** (default): Try UIA first. If 0 results, fall back to OCR.

**Method parameter**: `method="auto"` (default), `"uia"`, or `"ocr"` to force a specific path.

**Return format**: `{success, matches: [{text, bbox, confidence, source, ref_id}], match_count, method_used}`
- `bbox` is in **screen-absolute coordinates** (translated from image-relative using origin offset)
- `ref_id` is a string like `"ref_0"`, `"ref_1"` for reference in subsequent calls
- `source` is `"uia"` or `"ocr"`
- Max 20 matches, sorted by confidence descending
- Each match bbox center is directly usable with `cv_mouse_click(x=bbox.x + bbox.width//2, y=bbox.y + bbox.height//2)`

**Error handling**: If no matches found, return `{success: true, matches: [], match_count: 0}` — not an error.

### Feature 4: `cv_get_text` — Clean Text Extraction

**Signature**: `cv_get_text(hwnd: int, method: str = "auto") -> dict`

**Two-tier extraction**:
1. **UIA text extraction** (primary): Walk UIA tree, collect all `Name` and `Value` properties from text-bearing elements (Text, Edit, Document, DataItem control types). Preserve reading order via spatial sorting (top-to-bottom, left-to-right based on bounding rects — sort key: `(y // line_height_estimate, x)`).
2. **OCR fallback**: When UIA returns insufficient text (< 20 chars), capture window screenshot and run improved OCR pipeline (Feature 2). Return with `source: "ocr"` flag.

**Method parameter**: `method="auto"` (default), `"uia"`, or `"ocr"` to force a specific path.

**Return format**: `{success, text, source, line_count, confidence}`
- `text` is the full extracted text, newline-separated
- `source` is `"uia"`, `"ocr"`, or `"hybrid"`
- `confidence` is 1.0 for UIA, average word confidence for OCR

## 5. User Stories

1. As an automation agent, I want `cv_ocr` to return accurate bounding boxes for every detected word and line, so I can target specific text elements for clicking.
2. As an automation agent, I want OCR accuracy above 97% on English UI text regardless of my Windows locale, so I don't misread button labels or data fields.
3. As an automation agent, I want to say `cv_find(hwnd, "Submit button")` and get back screen-absolute coordinates of the Submit button, so I can interact with any app in one tool call.
4. As an automation agent, I want `cv_get_text(hwnd)` to extract all visible text from any Windows application with UIA accuracy when available and OCR fallback otherwise.
5. As an automation agent, I want to specify `lang="en"` on `cv_ocr` to force English OCR on a Spanish-locale machine when I know the target app is in English.
6. As an automation agent, I want OCR results to include per-word confidence scores so I can gauge reliability and decide whether to retry with preprocessing.

## 6. Non-Functional Requirements

- **Performance**: `cv_find` returns within 3s (UIA path) or 5s (OCR path). `cv_get_text` via UIA returns within 2s. OCR preprocessing adds no more than 200ms.
- **Backward compatibility**: `cv_ocr` output schema adds new fields (`words`, `confidence`, `origin`) but does NOT remove or rename existing fields. Existing callers are unaffected.
- **Security**:
  - `cv_find` and `cv_get_text` are read-only tools requiring `validate_hwnd_fresh` + `check_restricted` + `log_action` gates (same as `cv_read_ui`)
  - NOT subject to `check_rate_limit` or `guard_dry_run` (non-mutating)
- **Architecture**: New tools go in `src/tools/` and are auto-discovered. No edits to `server.py`. Use existing `make_error`/`make_success` patterns. All models use Pydantic `BaseModel`.
- **Zero new dependencies**: All features use existing deps (Pillow, winocr, comtypes, pywin32). Fuzzy matching uses stdlib `difflib.SequenceMatcher`.
- **Testing**: Unit tests with mocked Win32 APIs for all new code paths in `tests/unit/`. Integration tests for OCR accuracy benchmarking.
- **Error codes**: Add `FIND_NO_MATCH` for cv_find when 0 results (informational, not failure). Add `OCR_LOW_CONFIDENCE` when average confidence < 0.7.

## 7. Assumptions & Constraints

- winocr's `OcrLine.words` property exposes `bounding_rect` with `x, y, width, height` attributes (verified against winocr API)
- Windows OCR language packs are enumerable via `Windows.Media.Ocr.OcrEngine.AvailableRecognizerLanguages`
- UIA provides useful text for native Windows apps (Win32, WPF, WinForms) but returns 0 elements for Chrome/Electron app content areas
- Pillow's built-in filters (grayscale, sharpen, contrast) are sufficient for OCR preprocessing without external ML models
- `difflib.SequenceMatcher` provides adequate fuzzy matching for UI element names (no external NLP needed)
- Screen-absolute coordinate translation requires knowing the capture origin (window rect or region coords)
