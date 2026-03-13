import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.auth.jwt import decode_access_token
from src.engine.manager import get_manager

router = APIRouter()


@router.websocket("/ws/logs/{user_id}")
async def ws_logs(websocket: WebSocket, user_id: str):
    await websocket.accept()

    # JWT auth via HttpOnly cookie
    token = websocket.cookies.get("access_token")
    payload = decode_access_token(token) if token else None
    if not payload or payload.get("sub") != user_id:
        await websocket.send_text("[system] 未授权\n")
        await websocket.close(code=4403)
        return

    manager = get_manager()

    queue = manager.log_queues.get(user_id)
    if not queue:
        await websocket.send_text("[system] 自动交易未运行\n")
        await websocket.close()
        return

    # Replay recent log buffer so user sees history after navigating back
    buffered = manager.get_log_buffer(user_id)
    if buffered:
        await websocket.send_text("[system] ── 历史日志回放 ──\n")
        for line in buffered:
            await websocket.send_text(line)
        await websocket.send_text("[system] ── 实时日志 ──\n")

    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_text(msg)
            except asyncio.TimeoutError:
                await websocket.send_text("[ping]\n")
    except WebSocketDisconnect:
        pass
