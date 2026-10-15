import { useEffect, useRef, useState } from 'react';
import { WS_URL } from '../api';

// Keeps one auto-reconnecting WebSocket open and hands every message to `onMessage`.
export default function useLiveFeed(onMessage) {
  const [connected, setConnected] = useState(false);
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    let ws;
    let retry;
    let alive = true;

    const connect = () => {
      if (!alive) return;
      ws = new WebSocket(WS_URL);
      ws.onopen = () => alive && setConnected(true);
      ws.onclose = () => {
        if (!alive) return;
        setConnected(false);
        retry = setTimeout(connect, 3000);
      };
      ws.onmessage = (event) => {
        try {
          handlerRef.current(JSON.parse(event.data));
        } catch (e) {
          console.error(e);
        }
      };
    };

    connect();
    return () => {
      alive = false;
      clearTimeout(retry);
      if (ws?.readyState === WebSocket.CONNECTING) ws.onopen = () => ws.close();
      else ws?.close();
    };
  }, []);

  return connected;
}
