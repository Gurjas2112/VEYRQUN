from fastapi import WebSocket


class ConnectionManager:
    """Fans ledger entries (plus affected agent state) out to dashboard clients of the same workspace."""

    def __init__(self) -> None:
        self.active_connections: dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, workspace_id: str) -> None:
        await websocket.accept()
        self.active_connections[websocket] = str(workspace_id)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.pop(websocket, None)

    async def broadcast(self, message: dict) -> None:
        target = message.get("workspace_id")
        disconnected = []
        for connection, workspace in list(self.active_connections.items()):
            if target is not None and workspace != target:
                continue  # tenant isolation applies to the live feed too
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    async def publish(self, entry: dict, agents: list[dict] | None = None) -> None:
        message = dict(entry)
        if agents:
            message["_agents"] = agents
            if len(agents) == 1:
                message["_agent_snapshot"] = agents[0]
        await self.broadcast(message)


ws_manager = ConnectionManager()
