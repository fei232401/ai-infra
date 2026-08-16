"""estimate_tokens: /api/generate(prompt) 与 /v1/chat/completions(messages) 都要统计
回归: 只统计 messages 时 generate 请求复杂度恒为 low → rule-002(长请求→GPU) 永不命中
"""
from router import estimate_tokens


def test_counts_messages_content():
    body = {"messages": [{"role": "user", "content": "x" * 100}]}
    # 100 字符 × 0.5 = 50 tokens
    assert estimate_tokens(body) == 50


def test_counts_prompt_field():
    body = {"prompt": "x" * 100}
    assert estimate_tokens(body) == 50


def test_counts_both_when_present():
    body = {"messages": [{"role": "user", "content": "x" * 100}], "prompt": "x" * 100}
    assert estimate_tokens(body) == 100


def test_empty_body_is_zero():
    assert estimate_tokens({}) == 0


def test_generate_long_prompt_is_high_complexity():
    """e2e 回归: /api/generate 长 prompt 必须算成 high 复杂度, 命中 rule-002 → GPU
    high 阈值 = 1000 tokens = 2000 字符 (×0.5)"""
    body = {"prompt": "Kubernetes容器编排平台控制平面由API Server调度器组成。" * 100}  # 2500 字符 → 1250 tokens
    assert estimate_tokens(body) >= 1000
