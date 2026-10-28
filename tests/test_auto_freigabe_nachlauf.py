"""Jeder Workflow, der bei Pull Requests läuft, muss den Nachlauf der automatischen Freigabe auslösen.

Sonst bleibt die Freigabe nach einem Neustart dieses Checks rot hängen, obwohl alles grün ist
(Vorfall 28.10.2026, echo-releases#24). Gleiche Prüfung in scai-releases.
"""
from pathlib import Path
import re
import unittest

WORKFLOWS = Path(__file__).resolve().parents[1] / '.github/workflows'


def unquote(s):
    return s.strip().strip('"\'')


def pr_workflows():
    out = []
    for f in sorted(WORKFLOWS.glob('*.y*ml')):
        text = f.read_text(encoding='utf-8')
        name = re.search(r'^name:\s*(.+)$', text, re.M)
        assert name, f'{f.name}: kein name:'
        inline = re.search(r'^on:\s*\[([^\]]*)\]', text, re.M)
        single = re.search(r'^on:\s*([\w-]+)\s*$', text, re.M)
        block = re.search(r'^ {2}pull_request:', text, re.M)
        triggers = [unquote(t) for t in inline.group(1).split(',')] if inline else [single.group(1)] if single else []
        if block or 'pull_request' in triggers:
            out.append((f.name, unquote(name.group(1))))
    return out


def nachlauf_liste():
    text = (WORKFLOWS / 'auto-freigabe-nachlauf.yml').read_text(encoding='utf-8')
    m = re.search(r'^ {4}workflows:\s*\[(.*)\]\s*$', text, re.M)
    assert m, 'auto-freigabe-nachlauf.yml: workflows-Liste fehlt'
    return [unquote(n) for n in m.group(1).split(',')]


class NachlaufDecktAllePrWorkflows(unittest.TestCase):
    def test_findet_pr_workflows(self):
        self.assertIn('Freigabe-Tests', [n for _, n in pr_workflows()])

    def test_jeder_pr_workflow_loest_nachlauf_aus(self):
        liste = nachlauf_liste()
        fehlend = [(f, n) for f, n in pr_workflows() if n not in liste]
        self.assertEqual(fehlend, [], 'im Nachlauf (workflow_run.workflows) ergänzen')


if __name__ == '__main__':
    unittest.main()
