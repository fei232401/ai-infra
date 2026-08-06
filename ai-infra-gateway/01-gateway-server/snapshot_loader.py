"""CyberRouter 路由快照加载器 — 数据面消费控制面策略

从 ConfigMap 挂载文件 (cyberrouter-routing-snapshot/snapshot.json) 加载路由快照,
定期刷新 (Operator 5s 级更新, gateway 10s 级同步 — "慢路径定策略, 快路径执行")

快照结构 (由 cyberrouter Operator 编译, 见 cyberrouter/internal/snapshot):
{
  "version": "1", "policyName": "...", "rolloutPercentage": 100,
  "rules": [{"id": "...", "priority": 100,
             "condition": {"tokenCount": "< 100", "complexity": "high", "cacheHitProbability": "> 0.5"},
             "action": {"tier": "vllm-3b-service", "fallbackChain": [...]}}],
  "slo": {"low": 200, "medium": 1000, "high": 5000}, "generation": 1
}
"""
import hashlib
import json
import logging
import threading
import time

logger = logging.getLogger("gateway.snapshot")

# 与 gateway-deployment.yaml 的 volumeMount 保持一致
DEFAULT_SNAPSHOT_PATH = "/etc/cyberrouter/snapshot.json"


class SnapshotLoader:
    """加载 + 缓存 + 后台定期刷新路由快照"""

    def __init__(self, path=DEFAULT_SNAPSHOT_PATH, refresh_interval=10):
        self.path = path
        self.refresh_interval = refresh_interval
        self._snapshot = None
        self._hash = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        """后台线程定期重载 (ConfigMap 更新 → 挂载文件变化 → 重读)"""
        self._reload()
        threading.Thread(target=self._loop, daemon=True, name="snapshot-refresh").start()

    def _loop(self) -> None:
        while not self._stop.wait(self.refresh_interval):
            try:
                self._reload()
            except Exception:
                logger.exception("快照刷新异常")

    def _reload(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            data_hash = hashlib.sha256(
                json.dumps(data, sort_keys=True).encode()
            ).hexdigest()[:16]
            with self._lock:
                if self._hash == data_hash:
                    return  # 内容未变 (K8s ConfigMap 挂载用 symlink 原子替换, mtime 不可靠)
                self._snapshot, self._hash = data, data_hash
            logger.info(
                "快照已刷新 policy=%s rules=%d hash=%s",
                data.get("policyName"), len(data.get("rules", [])), data_hash,
            )
        except FileNotFoundError:
            # 启动可能早于 ConfigMap 就绪 (Operator 未同步或未部署)
            logger.warning("快照文件不存在: %s (等待 ConfigMap 挂载就绪)", self.path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("快照解析失败: %s", e)

    def get(self) -> dict:
        """返回当前快照 (可能为 None = 未就绪)"""
        with self._lock:
            return self._snapshot

    def hash(self) -> str:
        with self._lock:
            return self._hash
