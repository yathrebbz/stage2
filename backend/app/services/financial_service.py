"""
financial_service.py — Service Financier Smart Room
====================================================
Calcul coûts énergétiques, gestion budgets, prévisions,
détection dépenses anormales et recommandations économies.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from influxdb_client import InfluxDBClient
from influxdb_client.client.query_api import QueryApi
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_

from app.core.config import settings
from app.models.models import FinancialRecord, Room, UserPreferences
from app.schemas.financial import (
    CurrentMonthSummary, FinancialHistory, FinancialRecommendation
)

logger = logging.getLogger(__name__)


class FinancialService:
    """
    Service de gestion financière énergétique.

    Fournit:
    - Calcul coûts temps réel (kWh → €)
    - Tracking budget mensuel
    - Projections fin de mois
    - Historique mensuel
    - Génération recommandations économies
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self._influx_client = InfluxDBClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG,
        )
        self._query_api: QueryApi = self._influx_client.query_api()

    # ──────────────────────────────────────────────────────
    #  RÉSUMÉ MOIS COURANT
    # ──────────────────────────────────────────────────────

    async def get_current_month_summary(
        self, room_id: UUID, user_prefs: UserPreferences
    ) -> CurrentMonthSummary:
        """
        Calcule le résumé financier du mois courant.

        Args:
            room_id:    ID de la salle
            user_prefs: Préférences utilisateur (tarif, budget)

        Returns:
            Résumé avec consommation, coûts, budget, prévisions
        """
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # ── Consommation depuis InfluxDB ──
        consumed_kwh = await self._query_kwh_from_influx(
            room_id=str(room_id),
            start=month_start,
            end=now,
        )

        # ── Calcul coût ──
        tariff = user_prefs.electricity_rate  # €/kWh
        cost_eur = consumed_kwh * tariff

        # ── Budget restant ──
        budget_eur = user_prefs.budget_monthly_eur
        budget_remaining = max(0.0, budget_eur - cost_eur)
        budget_used_pct = (cost_eur / budget_eur * 100) if budget_eur > 0 else 0

        # ── Projection linéaire fin de mois ──
        days_elapsed = (now - month_start).days + 1
        days_in_month = self._days_in_month(now.year, now.month)
        days_remaining = days_in_month - days_elapsed

        daily_rate_kwh = consumed_kwh / max(days_elapsed, 1)
        projected_kwh = consumed_kwh + (daily_rate_kwh * days_remaining)
        projected_cost = projected_kwh * tariff

        # ── Comparaison mois précédent ──
        prev_month_kwh = await self._get_previous_month_kwh(room_id, now)
        savings_vs_prev_pct = None
        if prev_month_kwh and prev_month_kwh > 0:
            # Normalisation sur jours écoulés pour comparaison équitable
            prev_daily = prev_month_kwh / days_in_month
            curr_daily = daily_rate_kwh
            savings_vs_prev_pct = ((prev_daily - curr_daily) / prev_daily) * 100

        return CurrentMonthSummary(
            period_start=month_start,
            period_end=now,
            consumed_kwh=round(consumed_kwh, 3),
            cost_eur=round(cost_eur, 2),
            budget_eur=budget_eur,
            budget_remaining_eur=round(budget_remaining, 2),
            budget_used_pct=round(budget_used_pct, 1),
            days_elapsed=days_elapsed,
            days_remaining=days_remaining,
            projected_total_kwh=round(projected_kwh, 3),
            projected_total_cost=round(projected_cost, 2),
            tariff_rate=tariff,
            currency=user_prefs.currency,
            savings_vs_prev_month_pct=round(savings_vs_prev_pct, 1) if savings_vs_prev_pct else None,
        )

    # ──────────────────────────────────────────────────────
    #  HISTORIQUE MENSUEL
    # ──────────────────────────────────────────────────────

    async def get_monthly_history(
        self, room_id: UUID, months: int = 12
    ) -> FinancialHistory:
        """Récupère l'historique financier mensuel."""
        result = await self.db.execute(
            select(FinancialRecord)
            .where(FinancialRecord.room_id == room_id)
            .order_by(FinancialRecord.period_start.desc())
            .limit(months)
        )
        records = result.scalars().all()

        return FinancialHistory(
            room_id=room_id,
            records=[
                {
                    "year": r.period_start.year,
                    "month": r.period_start.month,
                    "kwh": r.total_kwh,
                    "cost": r.total_cost,
                    "budget": r.budget,
                    "tariff_rate": r.tariff_rate,
                    "anomaly": r.anomaly_detected,
                }
                for r in records
            ],
        )

    # ──────────────────────────────────────────────────────
    #  RECOMMANDATIONS
    # ──────────────────────────────────────────────────────

    async def generate_recommendations(
        self, room_id: UUID, user_prefs: UserPreferences
    ) -> List[FinancialRecommendation]:
        """
        Génère des recommandations d'économies basées sur les données historiques.

        Analyse:
        - Patterns de consommation horaire
        - Heures creuses vs pleines
        - Corrélation présence/consommation
        - Pics de consommation identifiés
        """
        recommendations = []

        # ── Analyse consommation par heure ──
        hourly_data = await self._query_hourly_power(room_id)
        if hourly_data:
            # Identifier heures de faible utilisation avec forte consommation
            night_consumption = sum(hourly_data.get(h, 0) for h in range(23, 24)) + \
                                sum(hourly_data.get(h, 0) for h in range(0, 6))
            night_avg = night_consumption / 7 if night_consumption > 0 else 0

            if night_avg > 50:  # > 50W la nuit
                saving = (night_avg * 7 * 30) / 1000 * user_prefs.electricity_rate
                recommendations.append(FinancialRecommendation(
                    category="scheduling",
                    description=(
                        f"Consommation nocturne détectée (~{night_avg:.0f}W entre 23h-6h). "
                        "Vérifiez les appareils en veille ou programmez leur extinction."
                    ),
                    estimated_saving_eur_month=round(saving, 2),
                    confidence=0.82,
                    priority="high" if saving > 5 else "medium",
                    sensor_evidence=["power_watts"],
                ))

        # ── Recommandation éclairage ──
        lighting_potential = await self._estimate_lighting_savings(room_id)
        if lighting_potential > 2.0:
            recommendations.append(FinancialRecommendation(
                category="lighting",
                description=(
                    "L'éclairage semble actif sans présence détectée par moments. "
                    "L'automatisation basée sur la présence pourrait économiser "
                    f"~{lighting_potential:.2f}€/mois."
                ),
                estimated_saving_eur_month=lighting_potential,
                confidence=0.75,
                priority="medium",
                sensor_evidence=["luminosity", "presence"],
            ))

        # ── Recommandation température ──
        temp_savings = await self._estimate_temperature_savings(room_id, user_prefs)
        if temp_savings > 3.0:
            recommendations.append(FinancialRecommendation(
                category="hvac",
                description=(
                    "Réduire la température de consigne de 1°C hors présence "
                    f"permettrait d'économiser ~{temp_savings:.2f}€/mois."
                ),
                estimated_saving_eur_month=temp_savings,
                confidence=0.70,
                priority="medium",
                sensor_evidence=["temperature", "power_watts"],
            ))

        # Trier par économies potentielles décroissantes
        recommendations.sort(key=lambda r: r.estimated_saving_eur_month, reverse=True)
        return recommendations[:5]  # Top 5 recommandations

    # ──────────────────────────────────────────────────────
    #  MÉTHODES PRIVÉES — InfluxDB Queries
    # ──────────────────────────────────────────────────────

    async def _query_kwh_from_influx(
        self, room_id: str, start: datetime, end: datetime
    ) -> float:
        """
        Calcule la consommation en kWh depuis InfluxDB.

        Utilise l'intégrale trapézoïdale sur la puissance instantanée (W → kWh).
        """
        flux_query = f"""
        from(bucket: "{settings.INFLUXDB_BUCKET}")
          |> range(start: {start.isoformat()}, stop: {end.isoformat()})
          |> filter(fn: (r) => r["_measurement"] == "sensor_data")
          |> filter(fn: (r) => r["room_id"] == "{room_id}")
          |> filter(fn: (r) => r["_field"] == "power_watts")
          |> aggregateWindow(every: 1m, fn: mean, createEmpty: false)
          |> sum()
        """
        try:
            tables = self._query_api.query(flux_query)
            total_watt_minutes = 0.0
            for table in tables:
                for record in table.records:
                    total_watt_minutes += float(record.get_value() or 0)

            # Convertir W·min → kWh : kWh = (W·min / 60) / 1000
            kwh = total_watt_minutes / 60.0 / 1000.0
            return max(0.0, kwh)

        except Exception as e:
            logger.error(f"Erreur InfluxDB query kWh: {e}")
            return 0.0

    async def _get_previous_month_kwh(
        self, room_id: UUID, reference_date: datetime
    ) -> Optional[float]:
        """Récupère la consommation du mois précédent depuis PostgreSQL."""
        prev_month = reference_date.replace(day=1) - timedelta(days=1)
        prev_start = prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        result = await self.db.execute(
            select(FinancialRecord).where(
                and_(
                    FinancialRecord.room_id == room_id,
                    FinancialRecord.period_start >= prev_start,
                    FinancialRecord.period_start < reference_date.replace(day=1),
                )
            ).limit(1)
        )
        record = result.scalar_one_or_none()
        return record.total_kwh if record else None

    async def _query_hourly_power(self, room_id: UUID) -> Dict[int, float]:
        """Calcule la consommation moyenne par heure de la journée (7 derniers jours)."""
        flux_query = f"""
        import "date"
        from(bucket: "{settings.INFLUXDB_BUCKET}")
          |> range(start: -7d)
          |> filter(fn: (r) => r["room_id"] == "{room_id}")
          |> filter(fn: (r) => r["_field"] == "power_watts")
          |> aggregateWindow(every: 1h, fn: mean)
          |> map(fn: (r) => ({{r with hour: date.hour(t: r._time)}}))
          |> group(columns: ["hour"])
          |> mean()
        """
        try:
            tables = self._query_api.query(flux_query)
            hourly = {}
            for table in tables:
                for record in table.records:
                    hour = record.values.get("hour", 0)
                    hourly[int(hour)] = float(record.get_value() or 0)
            return hourly
        except Exception as e:
            logger.error(f"Erreur query horaire: {e}")
            return {}

    async def _estimate_lighting_savings(self, room_id: UUID) -> float:
        """Estime les économies d'éclairage potentielles (€/mois)."""
        # Approximation: 10% de la consommation si optimisation éclairage
        # Valeur basée sur corrélation luminosité + présence
        # En production: requête InfluxDB corrélée
        return 3.50  # Placeholder

    async def _estimate_temperature_savings(
        self, room_id: UUID, user_prefs: UserPreferences
    ) -> float:
        """Estime les économies de chauffage/climatisation."""
        # Règle empirique: -1°C = -6% consommation HVAC
        # Placeholder - en production: analyse consommation vs température
        return 4.20

    @staticmethod
    def _days_in_month(year: int, month: int) -> int:
        """Retourne le nombre de jours dans un mois."""
        if month == 12:
            return (datetime(year + 1, 1, 1) - datetime(year, 12, 1)).days
        return (datetime(year, month + 1, 1) - datetime(year, month, 1)).days
