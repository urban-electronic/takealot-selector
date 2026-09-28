"""
图片代理端点 - 转发外部图片以绕过 CDN Referer 防盗链
"""

import os
from fastapi import APIRouter, Query, Header, HTTPException
from fastapi.responses import StreamingResponse
import httpx

router = APIRouter(prefix="/api", tags=["image-proxy"])

_API_KEY = os.environ.get("API_KEY", "").strip()


@router.get("/image-proxy")
async def image_proxy(
    url: str = Query(..., description="原始图片 URL"),
    token: str = Query("", description="图片鉴权 token（<img> 无法带 header，走 query）"),
    x_api_key: str = Header("", alias="X-API-Key"),
):
    """代理转发图片，携带 Takealot Referer 头以绕过 CDN 防盗链。

    鉴权说明：/api/image-proxy 从全局中间件跳过，因为 <img> 标签无法携带
    X-API-Key 头；此处改用 query token（前端 getImageUrl 自动拼接）校验，
    同时兼容 X-API-Key 头（fetch 场景），避免鉴权被绕过。
    """
    if _API_KEY and token != _API_KEY and x_api_key != _API_KEY:
        raise HTTPException(status_code=401, detail="无效或缺失 API Key")
    headers = {
        "Referer": "https://www.takealot.com/",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"上游图片返回 {resp.status_code}")
            content_type = resp.headers.get("content-type", "image/jpeg")
            return StreamingResponse(
                resp.aiter_bytes(),
                media_type=content_type,
                headers={"Cache-Control": "public, max-age=86400"},
            )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="请求上游图片超时")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"图片代理失败: {str(e)}")
