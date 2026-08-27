/**
 * Dashboard.tsx — Dashboard principal Smart Room
 * ===============================================
 * Vue temps réel : KPIs capteurs, statut devices, alertes récentes.
 * Actualisation via WebSocket (< 1s latence).
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  Activity, AlertTriangle, Battery, DollarSign,
  Droplets, Eye, Thermometer, Wind, Zap
} from "lucide-react";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { useRoomStore } from "@/store/roomStore";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useAlerts } from "@/hooks/useAlerts";
import { KPICard } from "@/components/ui/KPICard";
import { SensorSparkline } from "@/components/charts/SensorSparkline";
import { formatValue, getComfortColor, getTrendIcon } from "@/utils/formatters";
import type { SensorUpdate, RoomStatus } from "@/types/sensor";

// ─── Types ────────────────────────────────────────────────

interface DashboardProps {
  roomId: string;
}

// ─── Composant principal ──────────────────────────────────

const Dashboard: React.FC<DashboardProps> = ({ roomId }) => {
  const { currentRoom, updateSensorData, sensorHistory } = useRoomStore();
  const { alerts, unacknowledgedCount } = useAlerts(roomId);
  const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");

  // ── WebSocket temps réel ──
  const handleSensorUpdate = useCallback((data: SensorUpdate) => {
    updateSensorData(data);
    setLastUpdate(new Date());
  }, [updateSensorData]);

  const { isConnected } = useWebSocket({
    roomId,
    onSensorUpdate: handleSensorUpdate,
    onConnect: () => setConnectionStatus("connected"),
    onDisconnect: () => setConnectionStatus("disconnected"),
  });

  // Statut connexion WebSocket
  const statusColor = {
    connecting: "bg-yellow-400",
    connected: "bg-green-400",
    disconnected: "bg-red-400",
  }[connectionStatus];

  const room = currentRoom;
  if (!room) return <DashboardSkeleton />;

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">

      {/* ── Header ── */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">
            {room.name}
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Surveillance en temps réel · Mis à jour {lastUpdate?.toLocaleTimeString("fr-FR") ?? "—"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Indicateur connexion */}
          <div className="flex items-center gap-2 bg-gray-800 rounded-full px-3 py-1.5">
            <div className={`w-2 h-2 rounded-full ${statusColor} ${isConnected ? "animate-pulse" : ""}`} />
            <span className="text-xs text-gray-300 capitalize">{connectionStatus}</span>
          </div>

          {/* Alertes non acquittées */}
          {unacknowledgedCount > 0 && (
            <Badge variant="destructive" className="gap-1">
              <AlertTriangle className="h-3 w-3" />
              {unacknowledgedCount} alerte{unacknowledgedCount > 1 ? "s" : ""}
            </Badge>
          )}
        </div>
      </div>

      {/* ── Alertes critiques (si présentes) ── */}
      {alerts.filter(a => a.severity === "critical" || a.severity === "emergency").map(alert => (
        <Alert key={alert.id} variant="destructive" className="mb-4 border-red-800 bg-red-950">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            <strong>{alert.alert_type.replace("_", " ").toUpperCase()}</strong>
            {" — "}{alert.message}
          </AlertDescription>
        </Alert>
      ))}

      {/* ── KPI Cards ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
        <KPICard
          title="Température"
          value={formatValue(room.temperature, "°C")}
          icon={<Thermometer className="h-5 w-5" />}
          trend={room.temperature_trend}
          color={room.temperature > 28 ? "red" : room.temperature < 18 ? "blue" : "green"}
          subtitle={`Confort: ${room.comfort_index?.toFixed(0)}%`}
        />
        <KPICard
          title="Humidité"
          value={formatValue(room.humidity, "%")}
          icon={<Droplets className="h-5 w-5" />}
          trend={room.humidity_trend}
          color={room.humidity > 70 ? "orange" : room.humidity < 30 ? "yellow" : "green"}
        />
        <KPICard
          title="Luminosité"
          value={formatValue(room.luminosity_lux, " lux")}
          icon={<Eye className="h-5 w-5" />}
          subtitle={room.luminosity_lux > 500 ? "Lumière vive" : "Tamisé"}
          color="blue"
        />
        <KPICard
          title="Présence"
          value={room.presence ? "Détectée" : "Absente"}
          icon={<Activity className="h-5 w-5" />}
          color={room.presence ? "green" : "gray"}
          dot={room.presence}
        />
      </div>

      {/* ── Consommation électrique ── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        <KPICard
          title="Puissance"
          value={formatValue(room.power_watts, " W")}
          icon={<Zap className="h-5 w-5" />}
          trend={room.power_trend}
          color={room.power_watts > 2000 ? "red" : "emerald"}
          className="md:col-span-1"
        />
        <KPICard
          title="Coût aujourd'hui"
          value={`€${room.cost_today_eur?.toFixed(2) ?? "—"}`}
          icon={<DollarSign className="h-5 w-5" />}
          subtitle={`${room.kwh_today?.toFixed(2)} kWh`}
          color="violet"
        />
        <KPICard
          title="Qualité air (CO₂)"
          value={formatValue(room.co2_ppm, " ppm")}
          icon={<Wind className="h-5 w-5" />}
          color={room.co2_ppm > 1000 ? "orange" : "green"}
          subtitle={room.co2_ppm > 1000 ? "⚠ Ventiler" : "✓ Sain"}
        />
      </div>

      {/* ── Graphiques historique 24h ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        <Card className="bg-gray-900 border-gray-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-300 flex items-center gap-2">
              <Thermometer className="h-4 w-4 text-orange-400" />
              Température & Humidité — 24h
            </CardTitle>
          </CardHeader>
          <CardContent>
            <SensorSparkline
              data={sensorHistory.temperature}
              secondaryData={sensorHistory.humidity}
              primaryColor="#f97316"
              secondaryColor="#38bdf8"
              primaryLabel="Temp (°C)"
              secondaryLabel="Humidité (%)"
              height={180}
            />
          </CardContent>
        </Card>

        <Card className="bg-gray-900 border-gray-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-gray-300 flex items-center gap-2">
              <Zap className="h-4 w-4 text-yellow-400" />
              Consommation électrique — 24h
            </CardTitle>
          </CardHeader>
          <CardContent>
            <PowerChart data={sensorHistory.power} />
          </CardContent>
        </Card>
      </div>

      {/* ── Statut devices ── */}
      <DeviceStatusPanel roomId={roomId} />
    </div>
  );
};

// ─── Sous-composants ──────────────────────────────────────

const PowerChart: React.FC<{ data: Array<{ timestamp: string; value: number }> }> = ({ data }) => (
  <ResponsiveContainer width="100%" height={180}>
    <AreaChart data={data} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
      <defs>
        <linearGradient id="powerGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="5%" stopColor="#eab308" stopOpacity={0.3} />
          <stop offset="95%" stopColor="#eab308" stopOpacity={0} />
        </linearGradient>
      </defs>
      <XAxis
        dataKey="timestamp"
        tickFormatter={(v) => new Date(v).getHours() + "h"}
        tick={{ fill: "#6b7280", fontSize: 11 }}
        axisLine={false}
        tickLine={false}
        interval="preserveStartEnd"
      />
      <YAxis
        tick={{ fill: "#6b7280", fontSize: 11 }}
        axisLine={false}
        tickLine={false}
        tickFormatter={(v) => `${v}W`}
        width={50}
      />
      <Tooltip
        contentStyle={{ backgroundColor: "#1f2937", border: "none", borderRadius: "8px" }}
        labelStyle={{ color: "#9ca3af" }}
        formatter={(v: number) => [`${v.toFixed(0)} W`, "Puissance"]}
        labelFormatter={(l) => new Date(l).toLocaleTimeString("fr-FR")}
      />
      <Area
        type="monotone"
        dataKey="value"
        stroke="#eab308"
        strokeWidth={2}
        fill="url(#powerGrad)"
        dot={false}
        activeDot={{ r: 4, fill: "#eab308" }}
      />
    </AreaChart>
  </ResponsiveContainer>
);

const DeviceStatusPanel: React.FC<{ roomId: string }> = ({ roomId }) => {
  const { devices } = useRoomStore();

  return (
    <Card className="bg-gray-900 border-gray-800">
      <CardHeader>
        <CardTitle className="text-sm font-medium text-gray-300">
          État des Dispositifs
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {devices.map((device) => (
            <div
              key={device.id}
              className="flex items-center gap-3 bg-gray-800 rounded-lg p-3"
            >
              <div className={`w-2 h-2 rounded-full ${device.is_online ? "bg-green-400" : "bg-red-400"}`} />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-200 truncate">{device.device_name}</p>
                <p className="text-xs text-gray-500">
                  {device.firmware_version} · {device.device_type.toUpperCase()}
                </p>
              </div>
              <Badge
                variant={device.is_online ? "secondary" : "destructive"}
                className="text-xs"
              >
                {device.is_online ? "Online" : "Offline"}
              </Badge>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
};

const DashboardSkeleton: React.FC = () => (
  <div className="min-h-screen bg-gray-950 p-6 animate-pulse">
    <div className="h-8 bg-gray-800 rounded w-64 mb-8" />
    <div className="grid grid-cols-4 gap-4 mb-8">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="h-32 bg-gray-800 rounded-xl" />
      ))}
    </div>
    <div className="grid grid-cols-2 gap-6">
      <div className="h-64 bg-gray-800 rounded-xl" />
      <div className="h-64 bg-gray-800 rounded-xl" />
    </div>
  </div>
);

export default Dashboard;
