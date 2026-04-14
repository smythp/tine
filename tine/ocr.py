"""OCR text detection via RapidOCR.

Runs RapidOCR (ONNX-wrapped PP-OCRv4) on a screenshot PNG and returns a list
of detected text regions with logical-coordinate bounding boxes. Output is
ready to merge into the ref cache as ``ref_tN`` entries.

RapidOCR returns physical-pixel quadrilaterals. This module converts each
quad to an axis-aligned bbox and divides by the display scale factor so the
bboxes live in the same logical-coord space as AT-SPI2 refs — one click path
for both namespaces.

The RapidOCR engine is created lazily on first call and cached for the
process lifetime. Model load is ~2s; inference is ~2s per 1080p frame on CPU.
"""

DEFAULT_MIN_CONFIDENCE = 0.8

_engine = None


def _get_engine():
    """Lazy-load the RapidOCR engine. Cached for process lifetime."""
    global _engine
    if _engine is not None:
        return _engine
    try:
        from rapidocr import RapidOCR
    except ImportError as e:
        raise RuntimeError(
            "RapidOCR is not installed. Install with: pip install rapidocr onnxruntime"
        ) from e
    _engine = RapidOCR()
    return _engine


def _quad_to_bbox(quad):
    """Convert a RapidOCR quadrilateral (4 points) to axis-aligned bbox dict.

    RapidOCR returns polygon points in any orientation; we take the
    axis-aligned bounding rect since UI text is effectively always axis-aligned
    and this matches the AT-SPI2 bbox shape.
    """
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    x = int(min(xs))
    y = int(min(ys))
    w = int(max(xs)) - x
    h = int(max(ys)) - y
    return {'x': x, 'y': y, 'w': w, 'h': h}


def _scale_bbox(bbox, scale):
    """Divide bbox coordinates by display scale factor.

    Screenshots come back in physical pixels. AT-SPI2 refs live in logical
    coords (the coord space click_ref multiplies back up by scale). Converting
    OCR output the same way means both ref namespaces share one click path.
    """
    if scale == 1.0:
        return dict(bbox)
    return {
        'x': int(bbox['x'] / scale),
        'y': int(bbox['y'] / scale),
        'w': int(bbox['w'] / scale),
        'h': int(bbox['h'] / scale),
    }


def detect_text(image_path, scale=1.0, min_confidence=DEFAULT_MIN_CONFIDENCE):
    """Run OCR on a screenshot and return detected text regions.

    Args:
        image_path: Path to a screenshot PNG (physical pixels).
        scale: Display scale factor. OCR output is divided by this so bboxes
            are in the same logical-coord space as AT-SPI2 refs.
        min_confidence: Drop detections below this confidence threshold.

    Returns:
        List of dicts, each with keys:
            text (str): detected text, as returned by RapidOCR
            bbox (dict): axis-aligned bbox in logical coords, with
                'coord': 'screen' so click_ref skips window-offset adjustment
            confidence (float): RapidOCR confidence score in [0, 1]
    """
    engine = _get_engine()
    result = engine(image_path)

    boxes = getattr(result, 'boxes', None)
    txts = getattr(result, 'txts', None)
    scores = getattr(result, 'scores', None)
    if boxes is None or txts is None or scores is None:
        return []

    detections = []
    for quad, text, confidence in zip(boxes, txts, scores):
        if confidence < min_confidence:
            continue
        raw_bbox = _quad_to_bbox(quad)
        if raw_bbox['w'] <= 0 or raw_bbox['h'] <= 0:
            continue
        bbox = _scale_bbox(raw_bbox, scale)
        if bbox['w'] <= 0 or bbox['h'] <= 0:
            continue
        bbox['coord'] = 'screen'
        detections.append({
            'text': text,
            'bbox': bbox,
            'confidence': float(confidence),
        })
    return detections


def assign_text_refs(detections, start_index=1):
    """Assign ref_tN IDs to a list of detections.

    Args:
        detections: List from detect_text().
        start_index: First N to use (defaults to 1 → ref_t1).

    Returns:
        Dict mapping ref_id (str) → entry dict with keys: text, bbox, confidence.
    """
    cache = {}
    for i, det in enumerate(detections):
        ref_id = f'ref_t{start_index + i}'
        cache[ref_id] = {
            'text': det['text'],
            'bbox': det['bbox'],
            'confidence': det['confidence'],
        }
    return cache


def next_text_ref_index(ref_cache):
    """Highest existing ref_tN index + 1, or 1 if none."""
    max_idx = 0
    for key in ref_cache:
        if key.startswith('ref_t'):
            try:
                idx = int(key[len('ref_t'):])
            except ValueError:
                continue
            if idx > max_idx:
                max_idx = idx
    return max_idx + 1


def _bbox_center(bbox):
    return (bbox['x'] + bbox['w'] // 2, bbox['y'] + bbox['h'] // 2)


def _bbox_contains(outer, px, py):
    return (outer['x'] <= px <= outer['x'] + outer['w']
            and outer['y'] <= py <= outer['y'] + outer['h'])


def normalize_text(s):
    """Normalize text for match-time lookup.

    RapidOCR concatenates words in a line without spaces ("Log In" comes back
    as "LogIn", "Continue with Google" → "ContinueWithGoogle"). Agents type
    the natural form. Normalizing both sides (casefold + strip whitespace and
    punctuation) lets the lookup work either way.
    """
    if not isinstance(s, str):
        return ''
    return ''.join(ch for ch in s.casefold() if ch.isalnum())


def dedup_against_atspi(text_detections, atspi_cache, window_offset=(0, 0),
                        scale=1.0):
    """Drop OCR detections whose text is already covered by an AT-SPI2 ref.

    An OCR detection is suppressed when its center falls inside a named
    AT-SPI2 ref whose normalized name matches the OCR text. AT-SPI2 wins
    because it carries real semantics (role, actions, focusable state).

    Args:
        text_detections: List from detect_text().
        atspi_cache: Ref cache from tine.tree.assign_refs (may contain _app).
        window_offset: (x, y) offset to add to window-relative AT-SPI2 bboxes
            so they share a coord space with screen-absolute OCR bboxes.
        scale: Display scale factor — unused here (both inputs already in
            logical coords) but kept in the signature for future use.

    Returns:
        Filtered list (same shape as text_detections).
    """
    named_refs = []
    for ref_id, entry in atspi_cache.items():
        if ref_id == '_app' or not isinstance(entry, dict):
            continue
        name = entry.get('name', '')
        bbox = entry.get('bbox')
        if not name or not bbox:
            continue
        w = bbox.get('w', 0)
        h = bbox.get('h', 0)
        if w <= 0 or h <= 0:
            continue
        coord = bbox.get('coord', 'window')
        if coord == 'screen':
            abs_bbox = dict(bbox)
        else:
            abs_bbox = dict(bbox)
            abs_bbox['x'] = bbox.get('x', 0) + window_offset[0]
            abs_bbox['y'] = bbox.get('y', 0) + window_offset[1]
        named_refs.append((normalize_text(name), abs_bbox))

    if not named_refs:
        return list(text_detections)

    kept = []
    for det in text_detections:
        det_norm = normalize_text(det['text'])
        cx, cy = _bbox_center(det['bbox'])
        suppressed = False
        for ref_norm, ref_bbox in named_refs:
            if not ref_norm or ref_norm != det_norm:
                continue
            if _bbox_contains(ref_bbox, cx, cy):
                suppressed = True
                break
        if not suppressed:
            kept.append(det)
    return kept
