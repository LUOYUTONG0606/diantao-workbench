"""
本地 LightRAG 知识库 API 客户端
开发规范文档已存入 RAG，修改代码前先调 query() 获取相关规则。

RAG 服务地址: http://127.0.0.1:9621
"""
import time
import json
import os
import sys
from typing import Optional

BASE_URL = "http://127.0.0.1:9621"


def _safe(msg: str) -> str:
    """过滤 GBK 无法输出的字符"""
    if sys.stdout.encoding == 'gbk':
        return msg.encode('gbk', errors='replace').decode('gbk')
    return msg


def _post(path: str, body: dict, timeout: int = 60) -> dict:
    """向 RAG 服务发 POST 请求，返回 JSON 响应"""
    import requests as _req

    try:
        resp = _req.post(
            f"{BASE_URL}{path}",
            json=body,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(_safe(f"  ⚠ RAG 请求失败 [{path}]: {e}"))
        return {"error": str(e)}


def _get(path: str, timeout: int = 60) -> dict:
    """向 RAG 服务发 GET 请求"""
    import requests as _req

    try:
        resp = _req.get(f"{BASE_URL}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(_safe(f"  ⚠ RAG 请求失败 [{path}]: {e}"))
        return {"error": str(e)}


def _delete(path: str, timeout: int = 60) -> dict:
    """向 RAG 服务发 DELETE 请求"""
    import requests as _req

    try:
        resp = _req.delete(f"{BASE_URL}{path}", timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"  ⚠️ RAG 请求失败 [{path}]: {e}")
        return {"error": str(e)}


# ========================================================
# 🔍 查询
# ========================================================

def query(
    question: str,
    mode: str = "mix",
    top_k: int = 5,
    response_type: Optional[str] = None,
) -> str:
    """
    查询 RAG 知识库，返回回答文本。

    参数:
        question: 查询问题
        mode: 查询模式 local|global|hybrid|naive|mix|bypass（默认 mix）
        top_k: 检索 top k 结果
        response_type: 回答格式，如 "Multiple Paragraphs"
    返回:
        回答字符串，失败返回空字符串
    """
    body = {
        "query": question,
        "mode": mode,
        "top_k": top_k,
    }
    if response_type:
        body["response_type"] = response_type

    result = _post("/query", body)
    if "error" in result:
        print(_safe(f"  Query 失败: {result['error']}"))
        return ""
    return result.get("response", "")


def query_with_references(
    question: str,
    mode: str = "mix",
    top_k: int = 5,
    include_chunk_content: bool = False,
) -> dict:
    """
    查询 RAG 知识库，返回回答 + 参考资料。

    返回:
        {"response": str, "references": [{"reference_id": str, "file_path": str, ...}]}
    """
    body = {
        "query": question,
        "mode": mode,
        "top_k": top_k,
        "include_references": True,
        "include_chunk_content": include_chunk_content,
    }
    result = _post("/query", body)
    if "error" in result:
        print(f"  Query 失败: {result['error']}")
        return {"response": ""}
    return result


def query_data(
    question: str,
    mode: str = "mix",
    top_k: int = 5,
) -> dict:
    """
    查询 RAG 结构化数据（实体、关系、chunks）。
    返回结构化的 data + metadata，不含 LLM 生成的回答。
    """
    body = {
        "query": question,
        "mode": mode,
        "top_k": top_k,
    }
    result = _post("/query/data", body)
    if "error" in result:
        print(f"  Query 失败: {result['error']}")
        return {}
    return result.get("data", {})


# ========================================================
# 📝 插入文本
# ========================================================

def insert_text(
    text: str,
    file_source: Optional[str] = None,
    chunk_token_size: int = 1200,
) -> str:
    """
    将一段文本插入 RAG 知识库（异步处理）。

    返回:
        track_id，可用于追踪处理状态
    """
    body = {
        "text": text,
        "chunking": {
            "strategy": "fixed_token",
            "params": {"chunk_token_size": chunk_token_size},
        },
    }
    if file_source:
        body["file_source"] = file_source

    result = _post("/text", body)
    if "error" in result:
        print(f"  Insert 失败: {result['error']}")
        return ""
    return result.get("track_id", "")


def insert_texts(
    texts: list[str],
    file_sources: Optional[list[str]] = None,
    chunk_token_size: int = 1200,
) -> str:
    """
    批量插入多段文本到 RAG 知识库。

    返回:
        track_id
    """
    body = {
        "texts": texts,
        "chunking": {
            "strategy": "fixed_token",
            "params": {"chunk_token_size": chunk_token_size},
        },
    }
    if file_sources:
        body["file_sources"] = file_sources

    result = _post("/texts", body)
    if "error" in result:
        print(f"  Insert texts 失败: {result['error']}")
        return ""
    return result.get("track_id", "")


# ========================================================
# 📄 文档管理
# ========================================================

def upload_document(file_path: str) -> str:
    """
    上传文件到 RAG 知识库（支持 PDF/DOCX/TXT/MD 等）。

    参数:
        file_path: 本地文件绝对路径
    返回:
        track_id
    """
    import requests as _req

    if not os.path.isfile(file_path):
        print(f"  ⚠️ 文件不存在: {file_path}")
        return ""

    try:
        with open(file_path, "rb") as f:
            resp = _req.post(
                f"{BASE_URL}/documents/upload",
                files={"file": (os.path.basename(file_path), f)},
                timeout=120,
            )
        resp.raise_for_status()
        result = resp.json()
        return result.get("track_id", "")
    except Exception as e:
        print(f"  ⚠️ 上传文件失败: {e}")
        return ""


def list_documents(page: int = 1, page_size: int = 50) -> dict:
    """列出知识库中的文档"""
    result = _get(f"/documents?page={page}&page_size={page_size}")
    return result if "error" not in result else {}


def delete_document(doc_id: str) -> bool:
    """按 doc_id 删除文档"""
    result = _delete(f"/documents/{doc_id}")
    return "error" not in result


def track_status(track_id: str) -> dict:
    """追踪文档处理状态"""
    result = _get(f"/documents/track_status/{track_id}")
    return result if "error" not in result else {}


def scan_documents(api_key: Optional[str] = None) -> str:
    """触发 RAG 扫描输入目录中的新文件"""
    params = ""
    if api_key:
        params = f"?api_key_header_value={api_key}"
    result = _post(f"/documents/scan{params}", {})
    if "error" in result:
        return ""
    return result.get("track_id", "")


# ========================================================
# 🔧 便捷函数
# ========================================================

def query_rules(scope: str, detail: str = "") -> str:
    """
    快捷查询开发规范中的某类规则。

    参数:
        scope: 查询范围，如 "白名单"、"返回规范"、"黑名单"、"任务类型"
        detail: 补充细节，如 "查看3个精选宝贝"
    返回:
        规则说明文本
    """
    question = f"开发规范：{scope}相关规则"
    if detail:
        question += f"，具体到 {detail}"
    return query(question, mode="mix", top_k=3, response_type="Bullet Points")


def insert_change_log(log_text: str) -> str:
    """
    记录代码修改日志到 RAG（便于后续查询历史）。"""
    return insert_text(
        text=f"[改动日志 {time.strftime('%Y-%m-%d %H:%M')}] {log_text}",
        file_source="rag_client.py",
    )


# ========================================================
# 🧪 命令行测试入口
# ========================================================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python rag_client.py query <问题>        # 查询知识库")
        print("  python rag_client.py text <文本内容>      # 插入文本")
        print("  python rag_client.py file <文件路径>      # 上传文档")
        print("  python rag_client.py rules <范围>         # 快捷查询开发规范")
        print("示例:")
        print('  python rag_client.py query "白名单规则"')
        print('  python rag_client.py rules 返回规范')
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "query":
        q = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if q:
            result = query(q)
            print(result)
        else:
            print("请输入查询内容")

    elif cmd == "text":
        text = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if text:
            tid = insert_text(text)
            print(f"track_id: {tid}")
        else:
            print("请输入文本内容")

    elif cmd == "file":
        path = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if path:
            tid = upload_document(path)
            print(f"track_id: {tid}")
        else:
            print("请输入文件路径")

    elif cmd == "rules":
        scope = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if scope:
            result = query_rules(scope)
            print(result)
        else:
            print("请输入要查询的规范范围，如 白名单/返回规范/任务类型")
