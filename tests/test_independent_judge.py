"""Retired judge cannot be re-enabled by stale credentials."""
import asyncio
import unittest
from unittest.mock import Mock

from gpt_researcher.evaluation.independent_judge import judge_report
from backend.reporting.evaluation_report import assess_run


class RetiredJudgeTests(unittest.TestCase):
    def test_no_network_even_with_old_configuration(self):
        factory = Mock(side_effect=AssertionError('network forbidden'))
        result = asyncio.run(judge_report('', {}, {}, env={'JUDGE_ENABLED': 'true', 'JUDGE_API_KEY': 'test'}, client_factory=factory))
        self.assertEqual(result['status'], 'removed')
        factory.assert_not_called()

    def test_historical_model_score_is_not_new_acceptance(self):
        result = assess_run({'total_duration_seconds': 100, 'independent_judge': {
            'status': 'completed', 'entity': {'requirement_met': True}, 'source': {'requirement_met': True}}})
        self.assertEqual(result['overall'], '待复核')
