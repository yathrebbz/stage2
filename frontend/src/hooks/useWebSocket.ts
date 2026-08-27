/**
 * useWebSocket.ts — Hook WebSocket temps réel
 * ============================================
 * Connexion persistante avec reconnexion automatique,
 * gestion des events IoT et commandes actuateurs.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuthStore } from "@/store/authStore";
import type { SensorUpdate, AlertEvent, DeviceStatusEvent } from "@/types/sensor";

interface WebSocketOptions {
  roomId: string;
  onSensorUpdate?: (data: SensorUpdate) => void;
  onAlertTriggered?: (data: AlertEvent) => void;
  onDeviceStatus?: (data: DeviceStatusEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
}

interface WebSocketReturn {
  isConnected: boolean;
  sendCommand: (actuatorId: string, command: string, payload: object) => void;
  subscribeMetrics: (metrics: string[]) => void;
}

const WS_BASE_URL = import.meta.env.VITE_WS_URL || "wss://api.smartroom.example.com";
const MAX_RECONNECT_ATTEMPTS = 10;
const RECONNECT_BASE_DELAY_MS = 1000;

export function useWebSocket(options: WebSocketOptions): WebSocketReturn {
  const { token } = useAuthStore();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<NodeJS.Timeout>();
  const [isConnected, setIsConnected] = useState(false);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!token || !mountedRef.current) return;

    const url = `${WS_BASE_URL}/ws/rooms/${options.roomId}?token=${token}`;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        console.log(`[WS] Connecté à room ${options.roomId}`);
        reconnectAttemptRef.current = 0;
        setIsConnected(true);
        options.onConnect?.();

        // Souscription aux métriques principales
        ws.send(JSON.stringify({
          type: "subscribe_metrics",
          metrics: ["temperature", "humidity", "luminosity", "power", "presence", "co2"],
        }));
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;

        try {
          const msg = JSON.parse(event.data as string);

          switch (msg.type) {
            case "sensor_update":
              options.onSensorUpdate?.(msg.data as SensorUpdate);
              break;
            case "alert_triggered":
              options.onAlertTriggered?.(msg.data as AlertEvent);
              break;
            case "device_status":
              options.onDeviceStatus?.(msg.data as DeviceStatusEvent);
              break;
            case "ping":
              ws.send(JSON.stringify({ type: "pong" }));
              break;
            default:
              console.warn("[WS] Message type inconnu:", msg.type);
          }
        } catch (err) {
          console.error("[WS] Parse error:", err);
        }
      };

      ws.onerror = (err) => {
        console.error("[WS] Erreur:", err);
      };

      ws.onclose = (event) => {
        if (!mountedRef.current) return;
        setIsConnected(false);
        options.onDisconnect?.();

        console.log(`[WS] Déconnecté (code: ${event.code})`);

        // Reconnexion exponentielle (sauf déconnexion volontaire)
        if (event.code !== 1000 && reconnectAttemptRef.current < MAX_RECONNECT_ATTEMPTS) {
          const delay = Math.min(
            RECONNECT_BASE_DELAY_MS * Math.pow(2, reconnectAttemptRef.current),
            30000  // Max 30s
          );
          reconnectAttemptRef.current++;
          console.log(`[WS] Reconnexion dans ${delay}ms (tentative ${reconnectAttemptRef.current})`);
          reconnectTimerRef.current = setTimeout(connect, delay);
        }
      };

    } catch (err) {
      console.error("[WS] Erreur connexion:", err);
    }
  }, [options.roomId, token]);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close(1000, "Component unmounted");
    };
  }, [connect]);

  const sendCommand = useCallback((actuatorId: string, command: string, payload: object) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      console.error("[WS] Impossible d'envoyer: non connecté");
      return;
    }
    wsRef.current.send(JSON.stringify({
      type: "send_command",
      data: { actuator_id: actuatorId, command, payload },
    }));
  }, []);

  const subscribeMetrics = useCallback((metrics: string[]) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "subscribe_metrics", metrics }));
  }, []);

  return { isConnected, sendCommand, subscribeMetrics };
}
