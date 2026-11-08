"""Assignment completion waits across intermediate settled turns."""

import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

from lifecycle_case import WorkerLifecycleCase
import worker_lifecycle


class CompletionWaits(WorkerLifecycleCase):
    def watch_completion(self, request):
        """Run watch with a deterministic process-identity boundary, without requiring host ps access."""
        run = subprocess.run

        def boundary(argv, **kwargs):
            if argv[:1] == ['ps']:
                return subprocess.CompletedProcess(argv, 0, 'fixture process identity\n', '')
            return run(argv, **kwargs)

        with worker_lifecycle.connect(self.state) as db, patch('subprocess.run', side_effect=boundary):
            return worker_lifecycle.watch(db, self.state, 'ticket-1/1', request)

    def test_one_notified_wait_spans_turns_until_report_settled_and_no_jobs(self):
        self.register()
        self.ready()
        worker = self.root / 'worker.py'
        worker.write_text('''import json
from pathlib import Path
p = Path('turn')
turn = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(turn))
if turn >= 2:
    Path('report.md').write_text('result: ' + ('done' if turn >= 3 else 'pending'))
Path('worker.json').write_text(json.dumps({'settled': turn != 4, 'jobs': ['test-job'] if turn < 4 else []}))
''')
        completion = self.root / 'completion.py'
        completion.write_text('''import json, sys
from pathlib import Path
report = Path('report.md')
state = json.loads(Path('worker.json').read_text())
sys.exit(0 if report.exists() and report.read_text() == 'result: done'
         and state['settled'] and state['jobs'] == [] else 1)
''')
        request = {'argv': [sys.executable, str(worker)], 'timeout': 10,
                   'completion': [sys.executable, str(completion)],
                   'notify': {'argv': [sys.executable, '-c',
                       "import sys; open('parent.jsonl', 'a').write(sys.argv[1] + '\\n')", '{receipt}'],
                       'timeout': 2}}
        receipt, code = self.watch_completion(request)
        self.assertEqual((code, receipt['reason']), (0, 'settled'))
        self.assertEqual((self.root / 'turn').read_text(), '5')
        messages = [json.loads(line) for line in (self.root / 'parent.jsonl').read_text().splitlines()]
        self.assertEqual([row['kind'] for row in messages], ['channel-check', 'wait-finished'])
        with worker_lifecycle.connect(self.state) as db:
            self.assertEqual([row[0] for row in db.execute(
                "SELECT kind FROM events WHERE kind LIKE 'wait-%' ORDER BY id")], ['wait-started', 'wait-finished'])

    def test_a_failed_completion_check_returns_its_error_and_log(self):
        self.register()
        self.ready()
        receipt, code = self.watch_completion({
            'argv': [sys.executable, '-c', 'print("turn settled")'], 'timeout': 5,
            'completion': [sys.executable, '-c', 'print("job status unavailable"); raise SystemExit(7)']})
        self.assertEqual((code, receipt['reason']), (7, 'completion-error'))
        self.assertEqual(Path(receipt['log']).read_text(),
                         'turn settled\njob status unavailable\n')

    def test_pending_completion_keeps_the_original_deadline_without_launching_more_work(self):
        self.register()
        self.ready()
        wait = [sys.executable, '-c', 'pass']
        completion = [sys.executable, '-c', 'raise SystemExit(1)']
        launches = []
        popen = subprocess.Popen
        clock = time.monotonic
        real_sleep = time.sleep
        elapsed = [0]

        def launch(argv, **kwargs):
            launches.append(argv)
            return popen(argv, **kwargs)

        def sleep(seconds):
            if seconds == 1:
                elapsed[0] += 10
            else:
                real_sleep(seconds)

        with patch('subprocess.Popen', side_effect=launch), patch('time.sleep', side_effect=sleep), \
                patch('time.monotonic', side_effect=lambda: clock() + elapsed[0]):
            receipt, code = self.watch_completion({'argv': wait, 'completion': completion, 'timeout': 10})
        self.assertEqual((code, receipt['reason']), (124, 'timeout'))
        self.assertEqual(launches, [wait, completion])


if __name__ == '__main__':
    unittest.main()
