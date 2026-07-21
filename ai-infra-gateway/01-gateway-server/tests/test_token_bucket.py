"""令牌桶（Token Bucket）限流器单元测试

测试对象：gateway_server.TokenBucket
原理：桶有容量上限，按固定速率补充令牌，每次请求消耗一个令牌
"""

import time
import pytest
from gateway_server import TokenBucket


class TestTokenBucket:

    def test_initial_tokens_equal_capacity(self):
        """新桶的初始令牌数 = 容量"""
        tb = TokenBucket(capacity=10, refill_rate=5)
        assert tb.tokens == 10
        assert tb.capacity == 10

    def test_consume_reduces_tokens(self):
        """成功消费后令牌数减少"""
        tb = TokenBucket(capacity=10, refill_rate=0)
        assert tb.consume(1) is True
        assert tb.tokens == 9

    def test_consume_until_exhausted(self):
        """连续消费直到桶空，后续消费被拒绝"""
        tb = TokenBucket(capacity=5, refill_rate=0)
        for _ in range(5):
            assert tb.consume(1) is True
        assert tb.tokens == 0
        assert tb.consume(1) is False

    def test_consume_exceeds_available_fails(self):
        """请求数超过剩余令牌时失败，且不扣减令牌"""
        tb = TokenBucket(capacity=3, refill_rate=0)
        assert tb.consume(5) is False
        assert tb.tokens == 3

    def test_refill_over_time(self):
        """令牌随时间自动补充"""
        tb = TokenBucket(capacity=100, refill_rate=100)
        tb.tokens = 0
        tb.last_refill = time.monotonic() - 0.5  # 0.5s × 100/s = 50 tokens
        assert tb.consume(1) is True
        assert tb.tokens == pytest.approx(49, abs=2)

    def test_tokens_cap_at_capacity(self):
        """长时间未请求后，补充的令牌数不会超过桶容量"""
        tb = TokenBucket(capacity=5, refill_rate=1000)
        tb.tokens = 0
        tb.last_refill = time.monotonic() - 100  # 理论补充 100000 个
        tb.consume(0)  # 触发补充逻辑但不消耗
        assert tb.tokens == 5
