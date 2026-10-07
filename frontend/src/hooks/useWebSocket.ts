import { useEffect, useRef, useState, useCallback } from "react";

interface UseWebSocketOptions {
  url: string;
  onMessage?: (data: unknown) => void;
  reconnectInterval?: number;
}

export function useWebSocket({ url, onMessage, reconnectInterval = 3000 }: UseWebSocketOptions) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<number | null>(null);
  const closingRef = useRef(false);

  const connect = useCallback(() => {
    // The reconnect timer re-invokes ``open`` — the same closure (same
    // url / onMessage / reconnectInterval) this ``connect`` was built
    // with — rather than reading ``connect`` from inside its own
    // initializer.
    function open(): void {
      closingRef.current = false;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onclose = () => {
        setConnected(false);
        if (!closingRef.current) {
          reconnectTimer.current = window.setTimeout(open, reconnectInterval);
        }
      };
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          onMessage?.(data);
        } catch {
          // Ignore parse errors
        }
      };
    }
    open();
  }, [url, onMessage, reconnectInterval]);

  useEffect(() => {
    connect();
    return () => {
      closingRef.current = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected };
}
