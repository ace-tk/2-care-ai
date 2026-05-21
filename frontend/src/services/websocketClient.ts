import type { VoiceServerEvent } from '../types';

type EventCallback<T = any> = (data: T) => void;

class VoiceWebSocketClient {
  private ws: WebSocket | null = null;
  private listeners: Record<string, EventCallback[]> = {};
  private statusListeners: EventCallback<string>[] = [];

  constructor() {
    this.listeners = {};
  }

  connect(token: string): Promise<WebSocket> {
    return new Promise((resolve, reject) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        resolve(this.ws);
        return;
      }

      const wsBaseUrl = import.meta.env.VITE_WS_URL || 'ws://localhost:8000/api/v1';
      // Append token query param for authentication
      const wsUrl = `${wsBaseUrl}/voice/stream?token=${encodeURIComponent(token)}`;

      this.updateStatus('connecting');

      try {
        this.ws = new WebSocket(wsUrl);
      } catch (err) {
        this.updateStatus('error');
        reject(err);
        return;
      }

      this.ws.onopen = () => {
        this.updateStatus('connected');
        resolve(this.ws!);
      };

      this.ws.onmessage = (event) => {
        try {
          if (typeof event.data === 'string') {
            const parsed: VoiceServerEvent = JSON.parse(event.data);
            this.emit(parsed.event, parsed);
          } else {
            // Binary audio packets from server (if applicable)
            this.emit('binary_chunk', event.data);
          }
        } catch (e) {
          console.error('Error parsing WebSocket message:', e);
        }
      };

      this.ws.onerror = (err) => {
        console.error('WebSocket connection error:', err);
        this.updateStatus('error');
        reject(err);
      };

      this.ws.onclose = () => {
        this.updateStatus('disconnected');
        this.ws = null;
      };
    });
  }

  send(data: string | ArrayBuffer | Blob): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn('Cannot send: WebSocket is not connected.');
      return;
    }
    this.ws.send(data);
  }

  disconnect(): void {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.updateStatus('disconnected');
  }

  // Event subscription management
  on(event: string, callback: EventCallback): void {
    if (!this.listeners[event]) {
      this.listeners[event] = [];
    }
    this.listeners[event].push(callback);
  }

  off(event: string, callback: EventCallback): void {
    if (!this.listeners[event]) return;
    this.listeners[event] = this.listeners[event].filter((cb) => cb !== callback);
  }

  onStatusChange(callback: EventCallback<string>): void {
    this.statusListeners.push(callback);
  }

  offStatusChange(callback: EventCallback<string>): void {
    this.statusListeners = this.statusListeners.filter((cb) => cb !== callback);
  }

  private emit(event: string, data: any): void {
    if (this.listeners[event]) {
      this.listeners[event].forEach((callback) => callback(data));
    }
  }

  private updateStatus(status: 'disconnected' | 'connecting' | 'connected' | 'error'): void {
    this.statusListeners.forEach((callback) => callback(status));
  }
}

export const voiceWebSocketClient = new VoiceWebSocketClient();
export default voiceWebSocketClient;
