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


COORD_VERIFY_TOLERANCE = 20


def _abs_bbox_center(bbox, window_offset):
    """Absolute pixel center of an AT-SPI2 bbox, honouring coord space."""
    x = bbox.get('x', 0)
    y = bbox.get('y', 0)
    w = bbox.get('w', 0)
    h = bbox.get('h', 0)
    if bbox.get('coord', 'window') != 'screen':
        x += window_offset[0]
        y += window_offset[1]
    return (x + w // 2, y + h // 2)


def verify_atspi_coords(atspi_cache, text_detections, window_offset=(0, 0),
                        tolerance=COORD_VERIFY_TOLERANCE):
    """Cross-check named AT-SPI2 refs against OCR detections.

    For each named AT-SPI2 ref, look for OCR detections with the same
    normalized text. Group by normalized name, and within each group:

      * if the AT-SPI side and OCR side have the same non-zero count,
        sort both lists by (y, x) and pair them by index, then compare
        each pair's center-to-center distance against ``tolerance``;
      * if the counts differ or either side is empty, mark the AT-SPI
        refs in that group as ``unknown`` — we refuse to guess which
        goes with which.

    Mutates each touched AT-SPI ref entry in ``atspi_cache`` to add a
    ``coord_verified`` field with value ``'true'``, ``'false'`` or
    ``'unknown'``. Leaves unnamed refs alone (no field added).

    Args:
        atspi_cache: Ref cache from assign_refs (may contain _app).
        text_detections: List from detect_text().
        window_offset: (x, y) offset to add to window-relative AT-SPI2
            bboxes so they share a coord space with screen-absolute OCR
            bboxes.
        tolerance: Maximum center-to-center distance (pixels) for a
            matched pair to count as ``true``.

    Returns:
        Dict mapping ref_N id → the OCR detection object that disputes
        its position (when coord_verified is ``'false'``), or ``None``
        when there's no dispute. Lets the caller later resolve the
        disputed detection to a ``ref_tN`` id once text refs are assigned.
    """
    refs_by_name = {}
    for ref_id, entry in atspi_cache.items():
        if ref_id == '_app' or not isinstance(entry, dict):
            continue
        if ref_id.startswith('ref_t'):
            continue
        name = entry.get('name', '')
        bbox = entry.get('bbox')
        if not name or not bbox:
            continue
        w = bbox.get('w', 0)
        h = bbox.get('h', 0)
        if w <= 0 or h <= 0:
            continue
        norm = normalize_text(name)
        if not norm:
            continue
        center = _abs_bbox_center(bbox, window_offset)
        refs_by_name.setdefault(norm, []).append((ref_id, center, entry))

    dets_by_text = {}
    for det in text_detections:
        norm = normalize_text(det.get('text', ''))
        if not norm:
            continue
        bbox = det.get('bbox') or {}
        if bbox.get('w', 0) <= 0 or bbox.get('h', 0) <= 0:
            continue
        center = _bbox_center(bbox)
        dets_by_text.setdefault(norm, []).append((det, center))

    disputes = {}
    tol_sq = tolerance * tolerance

    for norm, ref_group in refs_by_name.items():
        det_group = dets_by_text.get(norm, [])
        if not det_group or len(ref_group) != len(det_group):
            for ref_id, _, entry in ref_group:
                entry['coord_verified'] = 'unknown'
                disputes[ref_id] = None
            continue
        refs_sorted = sorted(ref_group, key=lambda r: (r[1][1], r[1][0]))
        dets_sorted = sorted(det_group, key=lambda d: (d[1][1], d[1][0]))
        for (ref_id, ref_center, entry), (det, det_center) in zip(refs_sorted, dets_sorted):
            dx = ref_center[0] - det_center[0]
            dy = ref_center[1] - det_center[1]
            if dx * dx + dy * dy <= tol_sq:
                entry['coord_verified'] = 'true'
                disputes[ref_id] = None
            else:
                entry['coord_verified'] = 'false'
                disputes[ref_id] = det

    return disputes


def dedup_against_atspi(text_detections, atspi_cache, window_offset=(0, 0),
                        scale=1.0, preserve_ids=None):
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
        preserve_ids: Optional set of ``id()`` values. Detections whose
            Python id appears in this set are never suppressed, even if
            they would otherwise be deduped. Used by the coord-verification
            pass to keep disputed detections visible as ``ref_tN`` so the
            agent can see both sides of a disagreement.

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

    preserve = preserve_ids or set()
    kept = []
    for det in text_detections:
        if id(det) in preserve:
            kept.append(det)
            continue
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
