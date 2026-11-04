"""
LightRAG 辅助函数 - 直接在代码中调用本地 LightRAG 知识库
用法：
    from lightrag_helper import query_rag
    answer = query_rag("开发规范对日志格式有什么要求？")
"""
import json
import urllib.request
import urllib.error

LIGHTRAG_URL = "http://127.0.0.1:9621"
QUERY_TIMEOUT = 180  # 查询超时（秒）


def query_rag(question: str, mode: str = "mix") -> str:
    """
    查询 LightRAG 知识库

    参数:
        question: 你的问题（如"开发规范对日志格式有什么要求？"）
        mode: 查询模式
            "naive"   - 纯向量检索（最快）
            "local"   - 局部知识图谱（适合具体细节）
            "global"  - 全局知识图谱（适合总结归纳）
            "hybrid"  - 知识图谱+向量混合
            "mix"     - 多策略融合（默认推荐）

    返回:
        答案字符串
    """
    url = f"{LIGHTRAG_URL}/query"
    data = json.dumps({"query": question, "mode": mode}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("response", "无返回结果")
    except urllib.error.HTTPError as e:
        return f"[LightRAG HTTP 错误] {e.code}: {e.read().decode()[:200]}"
    except urllib.error.URLError as e:
        return f"[LightRAG 连接失败] 请确认 LightRAG 服务已启动: {e.reason}"
    except Exception as e:
        return f"[LightRAG 错误] {e}"


def upload_text_to_rag(text: str) -> dict:
    """
    上传文本到 LightRAG 知识库

    参数:
        text: 要索引的文本内容

    返回:
        包含上传结果的字典
    """
    url = f"{LIGHTRAG_URL}/documents/text"
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def get_rag_status() -> dict:
    """
    获取 LightRAG 知识库的文档状态

    返回:
        包含状态统计的字典
    """
    url = f"{LIGHTRAG_URL}/documents/status_counts"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


# 测试代码
if __name__ == "__main__":
    print("=== 测试 LightRAG 查询 ===")
    answer = query_rag("开发规范有哪些要求？")
    print(answer)
    print("\n=== 文档状态 ===")
    status = get_rag_status()
    print(status)
