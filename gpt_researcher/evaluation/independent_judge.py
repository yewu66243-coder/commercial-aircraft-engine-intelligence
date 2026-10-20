"""Compatibility for old offline scripts. The model judge has been removed.

Evidence selection remains available, but no credentials or network client is
read by this module. Historical JSON records are unchanged.
"""
from .review_samples import _evidence, build_samples


async def judge_report(*args, **kwargs):
    return {"status": "removed", "message": "独立模型裁判已移除，请使用人工核验流程。",
            "entity": {}, "source": {}, "results": []}
