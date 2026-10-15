from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from db import get_analysis, init_db, list_history, save_analysis
from llm import MODEL, analyze_company

BASE_DIR = Path(__file__).parent  # 项目根目录；拼路径都用它

app = FastAPI(title="AI 公司背调系统", version="0.1")

init_db()  # 启动时建表


class AnalyzeRequest(BaseModel):
    """前端提交上来的数据。缺 company 会直接返回 422。"""

    company: str
    extra: str = ""


@app.get("/")
def index():
    """把前端页面发回浏览器。"""
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    try:
        text = analyze_company(req.company, req.extra)
    except httpx.HTTPStatusError as e:
        return {
            "error": f"调用 LLM 失败：HTTP {e.response.status_code}",
            "detail": e.response.text[:300],
        }
    record_id = save_analysis(req.company, req.extra, text, MODEL)
    return {"id": record_id, "company": req.company, "analysis": text}


@app.get("/history")
def history(limit: int = 10):
    return {"items": list_history(limit)}


@app.get("/history/{record_id}")
def history_detail(record_id: int):
    row = get_analysis(record_id)
    if row is None:
        return {"error": f"没有 id={record_id} 的记录"}
    return row


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
