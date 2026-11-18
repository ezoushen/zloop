#!/usr/bin/env python3
"""Late quality signals stay visible without changing acceptance or double counting retries."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import support
import outcomes
import worker_lifecycle as life


class QualityFeedback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, XDG_STATE_HOME=str(self.root / 'xdg'))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = life.connect(self.root / 'workers.sqlite')
        self.addCleanup(self.db.close)
        report = self.root / 'report.md'
        report.write_text('## Implemented\nDone\n')
        row = dict(attempt='t/1', assignment='t', agent='builder', job='ticket', repo=str(self.root),
                   runtime='codex', requested={'model': 'model'}, execution_snapshot={'candidate': 'model'},
                   accepted={'commit': 'a'}, result={'status': 'completed', 'head': 'a',
                                                     'report': str(report), 'evidence': []})
        self.db.execute('INSERT INTO attempts VALUES (?,?)', ('t/1', json.dumps(row)))
        self.db.execute('INSERT INTO events (attempt,kind,payload,acknowledged) VALUES (?,?,?,1)',
                        ('t/1', 'result', json.dumps({'recorded_at': 10})))
        self.db.commit()

    def test_incident_retries_and_later_check_links_do_not_increment_the_defect(self):
        first = life.attribute(self.db, 't/1', 'owner-defect', 1, ['incident.md'], incident_id='I-1')
        again = life.attribute(self.db, 't/1', 'owner-defect', 1, ['incident.md'], incident_id='I-1',
                               feedback={'failure_class': 'false-pause', 'check': 'test_pause',
                                         'negative_control': 'red.log'})
        self.assertEqual(first['event_id'], again['event_id'])
        self.assertEqual(again['signals']['owner_defects'], 1)
        result = outcomes.quality_report(since=5, until=15)
        row = result['agents']['builder']['ticket']['model']
        self.assertEqual(row['successes'], 1)
        self.assertEqual(row['owner_defects'], 1)
        self.assertEqual(row['accepted_attempts'], 1)
        self.assertEqual(result['failure_classes']['false-pause']['incidents'], 1)
        self.assertEqual(result['failure_classes']['false-pause']['checks'], ['test_pause'])

    def test_missing_quality_or_cohort_data_stays_explicitly_unknown(self):
        outcomes.put('legacy', dict(agent='builder', job='ticket', candidate='legacy', status='succeeded', accepted=True))
        report = outcomes.quality_report(since=5, until=15)
        self.assertEqual(report['coverage']['unknown_completed_at'], 1)
        self.assertEqual(report['agents'], {})
        self.assertIn('unknown', report['coverage']['quality_observation'])


if __name__ == '__main__':
    unittest.main()
