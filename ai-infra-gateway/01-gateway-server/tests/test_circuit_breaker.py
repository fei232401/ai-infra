"""熔断器（Circuit Breaker）状态机单元测试

测试对象：gateway_server.CircuitBreaker
状态转换：CLOSED → (连续失败) → OPEN → (超时) → HALF_OPEN → (成功/失败) → CLOSED/OPEN
"""

import time
import pytest
from gateway_server import CircuitBreaker


class TestCircuitBreaker:

    def test_initial_state_closed(self):
        """初始状态为 CLOSED，允许请求通过"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=30)
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True

    def test_transition_to_open_after_threshold(self):
        """连续失败达到阈值后切换到 OPEN"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=30)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    def test_open_rejects_all_requests(self):
        """OPEN 状态下拒绝所有请求"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=30)
        for _ in range(3):
            cb.record_failure()
        assert cb.allow_request() is False

    def test_transition_to_half_open_after_timeout(self):
        """OPEN 状态超过超时时间后进入 HALF_OPEN"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=1)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        # 模拟超时已过
        cb.last_failure_time = time.monotonic() - 2
        assert cb.allow_request() is True
        assert cb.state == CircuitBreaker.HALF_OPEN

    def test_success_resets_to_closed(self):
        """HALF_OPEN 状态下成功后恢复为 CLOSED"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=30)
        cb.state = CircuitBreaker.HALF_OPEN
        cb.failure_count = 3
        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.failure_count == 0

    def test_failure_in_half_open_goes_to_open(self):
        """HALF_OPEN 状态下失败后重新熔断为 OPEN"""
        cb = CircuitBreaker(failure_threshold=3, timeout_seconds=1)
        for _ in range(3):
            cb.record_failure()
        cb.last_failure_time = time.monotonic() - 2
        cb.allow_request()  # → HALF_OPEN
        cb.record_failure()  # failure_count(4) >= threshold(3) → OPEN
        assert cb.state == CircuitBreaker.OPEN
