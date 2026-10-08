import importlib
import unittest


class ReportFrameworkTests(unittest.TestCase):
    def setUp(self):
        self.module = importlib.import_module('backend.reporting.framework')

    def test_airworthiness_task_uses_monitoring_framework(self):
        plan = self.module.build_report_framework_plan(
            task='商用航空发动机适航指令、服务通告与强制维修措施监测研究',
            report_type='detailed_report',
        )
        self.assertEqual(plan['id'], 'airworthiness_maintenance_monitoring')
        self.assertFalse(plan['public_method_section'])
        titles = [item['title'] for item in plan['chapters']]
        self.assertIn('适航响应与服务文件进展', titles)
        self.assertIn('维修执行与运营影响', titles)

    def test_patent_task_uses_patent_framework(self):
        plan = self.module.build_report_framework_plan(
            task='航空发动机涡轮冷却构型专利布局与核心申请人分析',
            report_type='research_report',
        )
        self.assertEqual(plan['id'], 'patent_landscape')
        text = self.module.format_framework_for_prompt(plan)
        self.assertIn('专利布局分析框架', text)
        self.assertIn('公开正文默认不设置“资料来源与研究方法”独立章节', text)
        self.assertIn('不能把材料来源逐条堆砌成事实清单', text)
        self.assertIn('反向提纲', text)


if __name__ == '__main__':
    unittest.main()
