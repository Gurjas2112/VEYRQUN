from typing import List, Optional

from fastapi import WebSocket


class ConnectionManager:
    """Fans every ledger entry (plus the affected agent state) out to dashboard clients."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def publish(self, entry: dict, agents: Optional[List[dict]] = None) -> None:
        message = dict(entry)
        if agents:
            message["_agents"] = agents
            if len(agents) == 1:
                message["_agent_snapshot"] = agents[0]
        await self.broadcast(message)


ws_manager = ConnectionManager()
