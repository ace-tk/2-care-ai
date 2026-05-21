import logging
from typing import Dict, Any
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class ConnectionManager:
    """
    Manages active WebSocket connections for multiple clients.
    Handles connection establishment, disconnection, and message broadcasting.
    """
    def __init__(self):
        # Maps session_id to WebSocket instance
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, session_id: str, websocket: WebSocket):
        """Accepts a new WebSocket connection and stores it."""
        await websocket.accept()
        self.active_connections[session_id] = websocket
        logger.info(f"WebSocket Connected: Client {session_id}. Total active connections: {len(self.active_connections)}")
        
        # Send a welcome/connection confirmed message
        await self.send_personal_message(
            {"event": "connected", "session_id": session_id, "payload": {"status": "success", "message": "Successfully connected to WebSocket server"}}, 
            session_id
        )

    def disconnect(self, session_id: str):
        """Removes a disconnected WebSocket from the active connections."""
        if session_id in self.active_connections:
            del self.active_connections[session_id]
            logger.info(f"WebSocket Disconnected: Client {session_id}. Total active connections: {len(self.active_connections)}")

    async def send_personal_message(self, message: dict, session_id: str):
        """Sends a JSON message to a specific client."""
        websocket = self.active_connections.get(session_id)
        if websocket:
            try:
                await websocket.send_json(message)
                logger.debug(f"Sent message to {session_id}: {message.get('event', 'unknown event')}")
            except Exception as e:
                logger.error(f"Failed to send message to {session_id}: {str(e)}")
                self.disconnect(session_id)

    async def broadcast(self, message: dict):
        """Broadcasts a JSON message to all connected clients."""
        logger.debug(f"Broadcasting message to {len(self.active_connections)} clients")
        # We need to iterate over a copy of the values in case connections drop during broadcast
        for session_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Failed to broadcast to {session_id}: {str(e)}")
                self.disconnect(session_id)

# Global connection manager instance
manager = ConnectionManager()
