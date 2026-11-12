"""示例部署必须保留已有配置，并对结果未知的创建只读回。"""
import copy
import shutil
import tempfile
from pathlib import Path
import unittest
from urllib.error import URLError

from scripts.install_example_teams import ROOT, definition, examples, install


class ExampleTeamInstallTests(unittest.TestCase):
    def setUp(self):
        self.rows = examples(ROOT / 'examples/teams', ['decision-lab'])
        self.current = {}
        self.posts = []
        self.mode = ''

    def call(self, path, payload=None):
        if payload is None:
            return {'agents': copy.deepcopy(list(self.current.values()))}
        self.posts.append(payload['name'])
        if self.mode != 'not_saved':
            self.current[payload['name']] = definition(payload)
        if self.mode == 'race':
            self.current[self.rows[-1]['name']] = {**self.rows[-1], 'description': '用户修改'}
        if self.mode in ('saved_error', 'not_saved'):
            raise URLError('连接中断')
        return {}

    def test_dependencies_precede_teams(self):
        positions = {row['name']: index for index, row in enumerate(self.rows)}
        for row in self.rows:
            for member in row['members']:
                if member['agent']:
                    self.assertLess(positions[member['agent']], positions[row['name']])

    def test_preview_never_posts(self):
        self.assertTrue(all(row['status'] == 'planned' for row in install(self.rows, self.call)))
        self.assertEqual(self.posts, [])

    def test_install_and_repeat_are_idempotent(self):
        install(self.rows, self.call, apply=True)
        self.assertEqual(self.posts, [row['name'] for row in self.rows])
        self.posts.clear()
        self.assertTrue(all(row['status'] == 'unchanged' for row in install(self.rows, self.call, apply=True)))
        self.assertEqual(self.posts, [])

    def test_conflict_or_disabled_stops_before_any_write(self):
        for override in ({'description': '用户修改'}, {'enabled': False}):
            self.current = {self.rows[-1]['name']: {**self.rows[-1], **override}}
            with self.assertRaises(ValueError):
                install(self.rows, self.call, apply=True)
            self.assertEqual(self.posts, [])

    def test_unknown_write_reads_back_without_replay(self):
        self.mode = 'saved_error'
        result = install(self.rows, self.call, apply=True)
        self.assertTrue(all(row['status'] == 'confirmed_after_error' for row in result))
        self.assertEqual(self.posts, [row['name'] for row in self.rows])

    def test_unknown_unsaved_write_fails_without_replay(self):
        self.mode = 'not_saved'
        with self.assertRaises(RuntimeError):
            install(self.rows, self.call, apply=True)
        self.assertEqual(len(self.posts), 1)

    def test_concurrent_conflict_is_not_silently_skipped(self):
        self.mode = 'race'
        with self.assertRaises(RuntimeError):
            install(self.rows, self.call, apply=True)
        self.assertNotIn(self.rows[-1]['name'], self.posts)

    def test_metadata_is_compared_without_normalization(self):
        row = self.rows[0]
        self.assertNotEqual(definition(row), definition({**row, 'metadata': {**row['metadata'], 'custom': None}}))
        self.assertNotEqual(definition(row), definition({**row, 'metadata': {**row['metadata'], 'personality_prompt': '独立人格正文'}}))

    def test_missing_dependency_and_duplicate_definition_reject(self):
        with self.assertRaises(ValueError):
            examples(ROOT / 'examples/teams', ['decision-lab', 'decision-lab'])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'decision-lab' / 'decision-lab-decision'
            target.mkdir(parents=True)
            shutil.copy(ROOT / 'examples/teams/decision-lab/decision-lab-decision/AGENTS.md', target)
            with self.assertRaisesRegex(ValueError, '缺少引用'):
                examples(Path(directory), ['decision-lab'])

    def test_learning_design_team_manifest_is_read_only_and_dependency_complete(self):
        rows = examples(ROOT / 'examples/teams', ['learning-design-lab'])
        teams = [row for row in rows if row['agent_type'] == 'team']
        self.assertEqual([row['name'] for row in teams], ['learning-design-lab-curriculum'])
        names = {row['name'] for row in rows}
        team = teams[0]
        self.assertEqual(team['metadata']['display_name'], '课程设计协作')
        self.assertEqual(sum(member['role'] == 'lead' for member in team['members']), 1)
        self.assertTrue(all(member['access'] == 'read' for member in team['members']))
        self.assertTrue(all(member.get('agent') in names for member in team['members'] if member.get('agent')))
        self.assertEqual(len(team['members']), 6)
