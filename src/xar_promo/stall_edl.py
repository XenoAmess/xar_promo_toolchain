"""Producer-neutral, metadata-only decisions for compressing verified stalls.

This library does not collect status, read media, run an encoder or approve a
film. Project adapters supply actual stop/progress endpoints and immutable
source evidence. Wallclock decisions remain estimates until PTS qualification.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math

SCHEMA = 'xar.recording-stall-edit-decision-list/v1'

def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('UTC offset required')
    return result.astimezone(timezone.utc)


def utc(value):
    return value.astimezone(timezone.utc).isoformat()


def segment_end(segment):
    if segment.get('duration_seconds') is None:
        return None
    from datetime import timedelta
    return stamp(segment['started_at_utc']) + timedelta(seconds=segment['duration_seconds'])


def overlaps(segments, start, end, role):
    pieces = []
    for segment in segments:
        first = max(start, stamp(segment['started_at_utc']))
        boundary = segment_end(segment)
        last = min(end, boundary) if boundary else end
        if last <= first:
            continue
        origin = stamp(segment['started_at_utc'])
        pieces.append({'session_id': segment['session_id'], 'role': role,
            'wallclock_start_utc': utc(first), 'wallclock_end_utc': utc(last),
            'estimated_source_start_seconds': (first - origin).total_seconds(),
            'estimated_source_end_seconds': (last - origin).total_seconds(),
            'raw_seconds': (last - first).total_seconds(),
            'PTS_mapping': 'UNVERIFIED_WALLCLOCK_ANCHOR_ESTIMATE', 'frame_exact': False})
    return pieces


def build(config, mode='speedup', keep_head=2.0, keep_tail=2.0, max_middle=4.0):
    if mode not in ('speedup', 'cut'):
        raise ValueError('Unknown editing mode')
    if any(not math.isfinite(x) or x < 0 for x in (keep_head, keep_tail)):
        raise ValueError('Head/tail durations must be finite and nonnegative')
    if not math.isfinite(max_middle) or not 0 < max_middle < 5:
        raise ValueError('Middle target must be strictly below 5 seconds')
    segments = sorted(deepcopy(config['segments']), key=lambda s: stamp(s['started_at_utc']))
    ids = [s['session_id'] for s in segments]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate recording session')
    for item in segments:
        stamp(item['started_at_utc'])
        duration = item.get('duration_seconds')
        if duration is not None and (not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0):
            raise ValueError('Invalid retained media duration')
        item.update(PTS_mapping='UNVERIFIED_WALLCLOCK_ANCHOR_ESTIMATE', frame_exact=False)
    gaps = []
    for previous, following in zip(segments, segments[1:]):
        end = segment_end(previous)
        if end is None:
            raise ValueError('A source preceding another segment must be closed')
        start = stamp(following['started_at_utc'])
        if start < end:
            raise ValueError('Overlapping estimated source timelines require explicit alignment')
        if start > end:
            gaps.append({'from_session': previous['session_id'], 'to_session': following['session_id'],
                'estimated_start_utc': utc(end), 'estimated_end_utc': utc(start),
                'estimated_seconds': (start - end).total_seconds(),
                'classification': 'NO_RECORDED_SOURCE_IN_SUPPLIED_SEGMENTS',
                'boundary_precision': 'unverified recording-start wallclock plus retained duration',
                'synthetic_gap_footage_created': False})
    output = {'schema': SCHEMA, 'created_at_utc': utc(datetime.now(timezone.utc)),
        'status': 'METADATA_EDIT_DECISIONS_ONLY',
        'policy': {'mode': mode, 'keep_error_head_seconds': keep_head,
            'keep_recovery_tail_seconds': keep_tail, 'max_recorded_middle_seconds': max_middle,
            'speed_factor_upper_bound': None, 'wait_end': config.get('wait_end_basis', 'first_verified_progress')},
        'segments': segments, 'source_gaps': gaps, 'intervals': [],
        'markers': deepcopy(config.get('markers', [])),
        'evidence_pins': deepcopy(config.get('evidence_pins', {})),
        'disclosures': ['Only real waiting footage is compressed; subsequent normal footage stays at original speed.',
            'Error head and actual recovery tail are retained; later repair notices are separate markers.',
            'All normal footage after first calendar progress, including later repair-result notices, remains uncompressed.',
            'Wallclock-to-PTS mapping is unverified and is not frame-exact.',
            'Original recordings, real runtime interruptions and source gaps remain disclosed.',
            'Active media was not probed, hashed or read; rendering requires closed sources and fresh quota admission.'],
        'runtime_calls': 0, 'media_body_bytes_read': 0}
    prior_end = None
    from datetime import timedelta
    for source in sorted(config['intervals'], key=lambda x: stamp(x['start_at_utc'])):
        item = deepcopy(source)
        start = stamp(item['start_at_utc'])
        end = stamp(item['end_at_utc']) if item.get('end_at_utc') else None
        if prior_end and start < prior_end:
            raise ValueError('Overlapping stalls must be reconciled before edit decisions')
        if end is None:
            item.update(status='OPEN_WAIT_END_PENDING', edit_actions=[])
            output['intervals'].append(item)
            continue
        if end < start:
            raise ValueError('Recovery precedes halt')
        prior_end = end
        raw = (end - start).total_seconds()
        head = min(keep_head, raw)
        tail = min(keep_tail, max(0.0, raw - head))
        middle_start, middle_end = start + timedelta(seconds=head), end - timedelta(seconds=tail)
        head_pieces = overlaps(segments, start, middle_start, 'keep_error_head')
        middle = overlaps(segments, middle_start, middle_end, 'waiting_middle')
        tail_pieces = overlaps(segments, middle_end, end, 'keep_actual_recovery_tail')
        recorded_middle = sum(p['raw_seconds'] for p in middle)
        target = min(recorded_middle, max_middle) if mode == 'speedup' else 0.0
        factor = recorded_middle / target if target else None
        for piece in head_pieces + tail_pieces:
            piece.update(action='keep', speed_factor=1.0, output_seconds=piece['raw_seconds'])
        for piece in middle:
            piece.update(action=mode, speed_factor=factor,
                         output_seconds=(piece['raw_seconds'] / factor) if factor else 0.0)
        actions = sorted(head_pieces + middle + tail_pieces, key=lambda x: stamp(x['wallclock_start_utc']))
        item.update(status='CLOSED_WAIT_ACTUAL_PROGRESS', wallclock_wait_seconds=raw,
            recorded_wait_seconds=sum(p['raw_seconds'] for p in actions),
            recorded_middle_seconds=recorded_middle, edited_middle_seconds=target,
            edited_wait_seconds=sum(p['output_seconds'] for p in actions),
            speed_factor=factor, edit_actions=actions,
            source_gap_seconds=max(0.0, raw - sum(p['raw_seconds'] for p in actions)),
            normal_footage_after_wait_end_compressed=False)
        output['intervals'].append(item)
    return output


def as_config(edl):
    return {key: deepcopy(edl.get(key, [] if key in ('segments', 'intervals', 'markers') else {}))
            for key in ('segments', 'intervals', 'markers', 'evidence_pins')}


def rebuild(config, old):
    p = old['policy']
    return build(config, p['mode'], p['keep_error_head_seconds'], p['keep_recovery_tail_seconds'], p['max_recorded_middle_seconds'])


def atempo_factors(speed):
    if not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed < 1:
        raise ValueError('Fast-forward factor must be finite and at least one')
    factors = []
    while speed > 2.0:
        factors.append(2.0)
        speed /= 2.0
    if abs(speed - 1.0) > 1e-12:
        factors.append(speed)
    return factors
