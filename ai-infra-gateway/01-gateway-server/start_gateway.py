"""Gateway launcher - 明确设置 cwd 后启动 uvicorn"""
import os
import sys

# 强制切换到项目根目录
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_DIR)
sys.path.insert(0, PROJECT_DIR)

print(f"[start_gateway] cwd={os.getcwd()}")
print(f"[start_gateway] python={sys.executable}")

if __name__ == "__main__":
    import uvicorn
    from gateway_server import config
    uvicorn.run(
        "gateway_server:app",
        host=config["server"]["host"],
        port=config["server"]["port"],
        reload=False,
        log_level="info",
        access_log=False,
    )