from copy import deepcopy
import math
import unittest
from xar_promo import stall_edl as e

class EditDecisions(unittest.TestCase):

    def config(self):
        return {'segments': [{'session_id': 'one', 'started_at_utc': '2026-10-17T00:00:00Z', 'duration_seconds': 10000, 'game_pid': 10, 'game_creation_filetime': 100, 'video_path': 'input.mkv'}], 'intervals': [{'id': 'a', 'start_at_utc': '2026-10-17T00:00:10Z', 'end_at_utc': '2026-10-17T00:10:10Z'}]}

    def test_default_middle_four_seconds_heads_tails_two(self):
        edl = e.build(self.config())
        item = edl['intervals'][0]
        self.assertEqual(item['edited_middle_seconds'], 4)
        self.assertEqual(item['edited_wait_seconds'], 8)
        self.assertEqual([a['output_seconds'] for a in item['edit_actions']], [2, 4, 2])
        self.assertEqual(item['speed_factor'], 149)
        self.assertFalse(item['normal_footage_after_wait_end_compressed'])

    def test_no_arbitrary_speed_cap_and_atempo_product(self):
        c = self.config()
        c['segments'][0]['duration_seconds'] = 100000
        c['intervals'][0]['end_at_utc'] = '2026-10-18T00:00:10Z'
        factor = e.build(c)['intervals'][0]['speed_factor']
        self.assertGreater(factor, 20000)
        self.assertAlmostEqual(math.prod(e.atempo_factors(factor)), factor)
        self.assertTrue(all((0.5 <= f <= 2 for f in e.atempo_factors(factor))))

    def test_short_wait_and_cut_optional(self):
        c = self.config()
        c['intervals'][0]['end_at_utc'] = '2026-10-17T00:00:13Z'
        item = e.build(c)['intervals'][0]
        self.assertEqual(item['edited_wait_seconds'], 3)
        self.assertEqual(item['edited_middle_seconds'], 0)
        self.assertEqual(e.build(self.config(), mode='cut')['intervals'][0]['edited_wait_seconds'], 4)
        with self.assertRaises(ValueError):
            e.build(c, max_middle=5)

    def test_cross_segment_gap_is_not_compressed_footage(self):
        c = self.config()
        c['segments'][0]['duration_seconds'] = 100
        c['segments'].append({**c['segments'][0], 'session_id': 'two', 'started_at_utc': '2026-10-17T00:05:00Z', 'duration_seconds': 10000})
        item = e.build(c)['intervals'][0]
        self.assertEqual(item['source_gap_seconds'], 200)
        self.assertEqual(item['edited_middle_seconds'], 4)
        self.assertEqual(len([a for a in item['edit_actions'] if a['role'] == 'waiting_middle']), 2)
        self.assertAlmostEqual(sum((a['output_seconds'] for a in item['edit_actions'] if a['role'] == 'waiting_middle')), 4)

    def test_open_wait_does_not_guess_future_end(self):
        c = self.config()
        c['intervals'][0]['end_at_utc'] = None
        item = e.build(c)['intervals'][0]
        self.assertEqual(item['status'], 'OPEN_WAIT_END_PENDING')
        self.assertEqual(item['edit_actions'], [])

class MetadataBoundaries(unittest.TestCase):
    def test_late_notice_and_overlay_metadata_are_preserved(self):
        config = EditDecisions().config()
        config['segments'][0]['overlay_retained'] = True
        config['markers'] = [{'at_utc': '2026-10-17T00:15:00Z', 'kind': 'late_notice'}]
        result = e.build(config)
        self.assertEqual(result['markers'], config['markers'])
        self.assertTrue(result['segments'][0]['overlay_retained'])
        self.assertEqual(result['intervals'][0]['end_at_utc'], '2026-10-17T00:10:10Z')

    def test_invalid_speed_factors_and_overlapping_sources_reject(self):
        for value in (0, -1, float('inf'), float('nan')):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): e.atempo_factors(value)
        config = EditDecisions().config()
        config['segments'].append({**config['segments'][0], 'session_id': 'other'})
        with self.assertRaises(ValueError): e.build(config)

if __name__ == '__main__': unittest.main()
