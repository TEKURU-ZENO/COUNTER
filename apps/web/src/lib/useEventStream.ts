"use client";

import { useEffect, useRef, useState } from "react";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

export interface OrderEvent {
  id: number;
  order_id: string;
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

// EventSource reconnects by itself and sends Last-Event-ID, so the server replays anything missed.
export function useEventStream(url: string | null, onEvent: (e: OrderEvent) => void): StreamStatus {
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!url) {
      setStatus("closed");
      return;
    }
    const source = new EventSource(url, { withCredentials: true });
    setStatus("connecting");
    source.onopen = () => setStatus("live");
    source.onerror = () => setStatus(source.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    source.addEventListener("order", (msg) => {
      try {
        handler.current(JSON.parse((msg as MessageEvent).data));
      } catch {
        /* ignore malformed event */
      }
    });
    return () => source.close();
  }, [url]);

  return status;
}
