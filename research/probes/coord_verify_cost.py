"""Probe: measure cost/value of ``verify_atspi_coords`` on live pages.

Feeds the decision in brief-20260418-33wp: is the coord-verification
layer (tagging every named AT-SPI ref with a ``coord_verified`` status
by cross-checking OCR) still earning its cost now that Firefox bboxes
are correct (c05fda3 / finding-20260418-3ff1)?

Runs against whatever app is focused and emits:

  * total wall-clock for the full ``describe(ocr=True)`` pipeline —
    tree walk, screenshot, OCR, verify, dedup, text-ref assignment,
    disputed_by linking. Matches what ``tine describe --ocr`` actually
    does so the verify fraction reflects real user-observed cost.
  * OCR inference time (RapidOCR invocation)
  * ``verify_atspi_coords`` time
  * post-verify steps time (dedup + assign + disputed_by linking) —
    kept as its own metric so decision-makers can see its weight
    separately from verify
  * fraction of total spent in verify
  * whether verify produced any ``'false'`` tag (a real dispute)
  * count of named AT-SPI refs and OCR detections
  * whether the option-3 fast-path was taken (short-circuit on no
    name overlap) — measured by comparing runs at
    ``TINE_COORD_VERIFY_FASTPATH=1`` and ``=0``

Usage on archgnome:

    ssh archgnome 'source /tmp/tine-env.sh && \
        python3 /home/patrick/projects/tine/research/probes/coord_verify_cost.py \
            --app Firefox --iterations 3'

    # No app arg = walk the whole desktop. Heavier but covers more refs.
    ssh archgnome 'source /tmp/tine-env.sh && \
        python3 /home/patrick/projects/tine/research/probes/coord_verify_cost.py \
            --iterations 3'

    # Only fast-path off, for baseline-only timing:
    ssh archgnome 'source /tmp/tine-env.sh && \
        python3 /home/patrick/projects/tine/research/probes/coord_verify_cost.py \
            --mode off'

The probe needs ``tine`` importable; running from the driver machine
won't work because tine.screenshot talks to the GNOME Shell extension
via D-Bus.

By default the probe runs one unmeasured warm-up iteration before the
measured runs so RapidOCR's ~2s model load (see tine/ocr.py docstring)
doesn't inflate the first measured iteration. Pass ``--no-warmup`` to
skip the warm-up and report iter-0 cold-start timing instead.

Emits one JSON object per iteration on stdout. Pipe into ``jq`` or save
for aggregation; the probe itself does not aggregate — a day's worth of
runs is easier to reason about as raw rows.

Side effects: each iteration captures a screenshot into ``$TMPDIR`` and
deletes it at exit. Temporarily sets the ``TINE_COORD_VERIFY_FASTPATH``
env var and restores it on exit. No clicks, no input injection.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time


def _ns():
    return time.perf_counter_ns()


def _ms(dt_ns):
    return dt_ns / 1e6


def _import_tine():
    """Import tine modules. Done inside main so ``--help`` works without
    the full tine install on PATH — useful when reading this file outside
    archgnome.
    """
    import tine.ocr as ocr_mod
    import tine.screenshot as screenshot_mod
    import tine.tree as tree_mod
    import tine.walker as walker_mod
    import tine.window_offset as window_offset_mod
    return {
        'ocr': ocr_mod,
        'screenshot': screenshot_mod,
        'tree': tree_mod,
        'walker': walker_mod,
        'window_offset': window_offset_mod,
    }


def run_once(app, fastpath_mode, scale, mods):
    """Time one full describe(ocr=True)-equivalent pipeline with fastpath toggled.

    Matches tine.describe.describe(ocr=True) step by step — including
    the post-verify dedup + text-ref assignment + disputed_by linking
    that the real describe pipeline runs after verify_atspi_coords.
    Without those steps t_total underestimates real user-observed cost
    and inflates verify_fraction_of_total, which can push the option
    decision the wrong way. See finding-20260419-3dv7.

    fastpath_mode: '1' (on — default) or '0' (force full pairing).
    Restores the TINE_COORD_VERIFY_FASTPATH env var on exit.
    Returns a dict of timings and outcomes.
    """
    env_var = mods['ocr'].COORD_VERIFY_FASTPATH_ENV
    prior_env = os.environ.get(env_var)
    os.environ[env_var] = fastpath_mode

    try:
        t0 = _ns()
        raw_tree = mods['walker'].walk_atspi(app=app)
        collapsed = mods['tree'].collapse_containers(raw_tree)
        _annotated, ref_cache = mods['tree'].assign_refs(collapsed)
        app_name = (raw_tree.get('name', '')
                    or raw_tree.get('application', {}).get('name', ''))
        t_tree = _ns() - t0

        named_refs_count = sum(
            1 for k, v in ref_cache.items()
            if k != '_app' and isinstance(v, dict)
            and v.get('name') and v.get('bbox')
        )

        fd, screenshot_path = tempfile.mkstemp(
            suffix='.png', prefix='coord-verify-probe-',
        )
        os.close(fd)

        try:
            t_capture_start = _ns()
            mods['screenshot'].capture(screenshot_path)
            t_capture = _ns() - t_capture_start

            t_ocr_start = _ns()
            detections = mods['ocr'].detect_text(screenshot_path, scale=scale)
            t_ocr = _ns() - t_ocr_start

            window_offset = mods['window_offset'].resolve_window_offset(app_name)

            t_verify_start = _ns()
            disputes = mods['ocr'].verify_atspi_coords(
                ref_cache, detections, window_offset=window_offset,
            )
            t_verify = _ns() - t_verify_start

            # Post-verify steps — what tine.describe.describe(ocr=True)
            # runs after verify_atspi_coords. Timed together because they
            # share work (both iterate detections x named refs) and because
            # the decision rule in the brief cares about total describe
            # cost, not step-by-step breakdown of the post-verify stage.
            t_post_start = _ns()
            kept = mods['ocr'].dedup_against_atspi(
                detections, ref_cache, window_offset=window_offset, scale=scale,
            )
            start_index = mods['ocr'].next_text_ref_index(ref_cache)
            text_refs = mods['ocr'].assign_text_refs(kept, start_index=start_index)
            ref_cache.update(text_refs)

            text_ref_order = list(text_refs.keys())
            for ref_tid, det in zip(text_ref_order, kept):
                disputed_ref = det.get('_disputed_ref_id') if isinstance(det, dict) else None
                if not disputed_ref:
                    continue
                entry = ref_cache.get(disputed_ref)
                if isinstance(entry, dict):
                    entry['disputed_by'] = ref_tid
            t_post = _ns() - t_post_start

            false_count = sum(1 for v in disputes.values() if v is True)
            unknown_count = sum(
                1 for k, v in ref_cache.items()
                if k != '_app' and isinstance(v, dict)
                and v.get('coord_verified') == 'unknown'
            )
            true_count = sum(
                1 for k, v in ref_cache.items()
                if k != '_app' and isinstance(v, dict)
                and v.get('coord_verified') == 'true'
            )
        finally:
            try:
                os.unlink(screenshot_path)
            except OSError:
                pass

        t_total = _ns() - t0
    finally:
        if prior_env is None:
            os.environ.pop(env_var, None)
        else:
            os.environ[env_var] = prior_env

    return {
        'fastpath_mode': fastpath_mode,
        'app_name': app_name,
        'app_arg': app,
        't_total_ms': round(_ms(t_total), 2),
        't_tree_ms': round(_ms(t_tree), 2),
        't_capture_ms': round(_ms(t_capture), 2),
        't_ocr_ms': round(_ms(t_ocr), 2),
        't_verify_ms': round(_ms(t_verify), 2),
        't_post_verify_ms': round(_ms(t_post), 2),
        'verify_fraction_of_total': round(t_verify / t_total, 4) if t_total else None,
        'named_refs': named_refs_count,
        'ocr_detections': len(detections),
        'kept_detections': len(kept),
        'false_tags': false_count,
        'true_tags': true_count,
        'unknown_tags': unknown_count,
        'verify_found_dispute': false_count > 0,
    }


def summarize(runs):
    """Optional at-a-glance summary so the JSON output can be skimmed.

    Returns a dict the caller may print; the decision (option 1/2/3)
    comes from reading the raw rows, not this summary.
    """
    if not runs:
        return {}

    def med(key):
        vs = [r[key] for r in runs if r.get(key) is not None]
        return round(statistics.median(vs), 2) if vs else None

    dispute_rate = (
        sum(1 for r in runs if r.get('verify_found_dispute')) / len(runs)
    )
    return {
        'iterations': len(runs),
        'median_total_ms': med('t_total_ms'),
        'median_ocr_ms': med('t_ocr_ms'),
        'median_verify_ms': med('t_verify_ms'),
        'median_post_verify_ms': med('t_post_verify_ms'),
        'median_verify_fraction': med('verify_fraction_of_total'),
        'dispute_rate': round(dispute_rate, 3),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Measure verify_atspi_coords cost/value. '
                    'See brief-20260418-33wp.'
    )
    parser.add_argument(
        '--app', default=None,
        help='App name to scope the AT-SPI walk to (e.g. Firefox). '
             'Default: walk the whole desktop.'
    )
    parser.add_argument(
        '--iterations', type=int, default=1,
        help='Iterations per mode. Higher reduces noise.'
    )
    parser.add_argument(
        '--mode', choices=['on', 'off', 'both'], default='both',
        help='Which fast-path mode to test. "both" runs each iteration '
             'twice (on then off) so the pairs can be compared directly.'
    )
    parser.add_argument(
        '--scale', type=float, default=1.0,
        help='Display scale factor passed to OCR. Keep at 1.0 unless '
             'testing a HiDPI setup.'
    )
    parser.add_argument(
        '--summary', action='store_true',
        help='Also print a single-line summary of medians + dispute rate '
             'at the end.'
    )
    parser.add_argument(
        '--no-warmup', action='store_true',
        help='Skip the unmeasured warm-up iteration. Measured iter 0 will '
             'then include RapidOCR model load (~2s) and look much slower '
             'than subsequent iterations.'
    )
    args = parser.parse_args(argv)

    mods = _import_tine()

    modes = {
        'on': ['1'],
        'off': ['0'],
        'both': ['1', '0'],
    }[args.mode]

    # Warm-up: run once in fast-path-on mode to prime the RapidOCR model
    # cache and the Mutter screenshot pipeline. Results discarded.
    if not args.no_warmup:
        run_once(args.app, '1', args.scale, mods)

    runs = []
    for iteration in range(args.iterations):
        for mode in modes:
            row = run_once(args.app, mode, args.scale, mods)
            row['iteration'] = iteration
            runs.append(row)
            # Flush per row so long runs are not lost on signal.
            sys.stdout.write(json.dumps(row) + '\n')
            sys.stdout.flush()

    if args.summary:
        sys.stdout.write(json.dumps({
            'summary_all': summarize(runs),
            'summary_fastpath_on': summarize(
                [r for r in runs if r['fastpath_mode'] == '1'],
            ),
            'summary_fastpath_off': summarize(
                [r for r in runs if r['fastpath_mode'] == '0'],
            ),
        }, indent=2) + '\n')


if __name__ == '__main__':
    main()
