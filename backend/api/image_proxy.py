"""
图片代理端点 - 转发外部图片以绕过 CDN Referer 防盗链
"""

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import StreamingResponse
import httpx

router = APIRouter(prefix="/api", tags=["image-proxy"])


@router.get("/image-proxy")
async def image_proxy(url: str = Query(..., description="原始图片 URL")):
    """代理转发图片，携带 Takealot Referer 头以绕过 CDN 防盗链"""
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
