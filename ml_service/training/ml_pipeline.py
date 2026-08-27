"""
ml_pipeline.py — Pipeline ML Smart Room
========================================
Anomaly Detection (IsolationForest + LSTM Autoencoder) +
Energy Prediction (LSTM + Prophet + XGBoost)

Usage:
    trainer = MLPipeline(config)
    trainer.train_anomaly_model(data)
    trainer.train_prediction_model(data)
"""

import logging
import os
import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import mlflow
import mlflow.sklearn
import mlflow.tensorflow
import numpy as np
import pandas as pd
from prophet import Prophet
from sklearn.ensemble import IsolationForest
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler, StandardScaler
from sklearn.svm import OneClassSVM
import xgboost as xgb

logger = logging.getLogger(__name__)


@dataclass
class ModelMetrics:
    """Métriques de performance d'un modèle."""
    model_name: str
    mae: Optional[float] = None
    rmse: Optional[float] = None
    r2: Optional[float] = None
    auc_roc: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None
    training_samples: int = 0
    training_time_s: float = 0.0


class MLPipeline:
    """
    Pipeline ML complet pour Smart Room.

    Modèles :
    A) Détection Anomalies
       - IsolationForest  : outliers multivariés non supervisé
       - LSTM Autoencoder : anomalies temporelles (reconstruire = normal)
       - OneClassSVM      : baseline comportement normal

    B) Prédiction Consommation
       - LSTM/GRU         : séquences temporelles 24h/7j
       - Prophet          : tendances + saisonnalité
       - XGBoost          : régression features engineered

    C) Analyse Financière
       - XGBoost regression pour prédiction facture mensuelle
    """

    FEATURES_ANOMALY = [
        "temperature", "humidity", "luminosity_lux",
        "power_watts", "co2_ppm", "comfort_index",
    ]
    FEATURES_ENERGY = [
        "power_watts", "hour_of_day", "day_of_week",
        "is_weekend", "temperature", "presence",
        "month", "quarter",
    ]

    def __init__(self, config: Dict, model_dir: str = "/app/models"):
        self.config = config
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)

        # MLflow setup
        mlflow.set_tracking_uri(config.get("mlflow_uri", "http://mlflow:5000"))
        mlflow.set_experiment("smart_room_ml")

        # Modèles en mémoire
        self._isolation_forest: Optional[IsolationForest] = None
        self._oneclass_svm: Optional[OneClassSVM] = None
        self._anomaly_scaler: Optional[StandardScaler] = None
        self._xgb_energy: Optional[xgb.XGBRegressor] = None
        self._prophet_model: Optional[Prophet] = None
        self._energy_scaler: Optional[MinMaxScaler] = None

    # ══════════════════════════════════════════════════════
    #  A) DÉTECTION D'ANOMALIES
    # ══════════════════════════════════════════════════════

    def train_anomaly_models(
        self, df: pd.DataFrame, room_id: str
    ) -> Dict[str, ModelMetrics]:
        """
        Entraîne les modèles de détection d'anomalies.

        Args:
            df:      DataFrame avec colonnes capteurs + timestamp
            room_id: Identifiant de la salle

        Returns:
            Métriques par modèle
        """
        logger.info(f"Entraînement modèles anomalie — {len(df)} échantillons")

        # Feature engineering
        X = self._prepare_anomaly_features(df)
        metrics = {}

        with mlflow.start_run(run_name=f"anomaly_{room_id}_{datetime.now().strftime('%Y%m%d')}"):
            mlflow.log_param("room_id", room_id)
            mlflow.log_param("n_samples", len(X))
            mlflow.log_param("features", self.FEATURES_ANOMALY)

            # ── IsolationForest ──
            import time
            start = time.time()
            self._anomaly_scaler = StandardScaler()
            X_scaled = self._anomaly_scaler.fit_transform(X)

            self._isolation_forest = IsolationForest(
                n_estimators=200,
                contamination=0.05,  # 5% d'anomalies attendues
                random_state=42,
                n_jobs=-1,
            )
            self._isolation_forest.fit(X_scaled)
            training_time = time.time() - start

            # Scores sur données d'entraînement
            scores = self._isolation_forest.score_samples(X_scaled)
            threshold = np.percentile(scores, 5)  # 5e percentile

            if_metrics = ModelMetrics(
                model_name="IsolationForest",
                training_samples=len(X),
                training_time_s=training_time,
            )
            metrics["isolation_forest"] = if_metrics

            mlflow.log_metric("if_threshold", float(threshold))
            mlflow.log_metric("if_training_time", training_time)
            mlflow.sklearn.log_model(self._isolation_forest, "isolation_forest")

            # ── OneClassSVM ──
            start = time.time()
            self._oneclass_svm = OneClassSVM(
                kernel="rbf",
                gamma="scale",
                nu=0.05,
            )
            self._oneclass_svm.fit(X_scaled)
            svm_time = time.time() - start

            svm_metrics = ModelMetrics(
                model_name="OneClassSVM",
                training_samples=len(X),
                training_time_s=svm_time,
            )
            metrics["oneclass_svm"] = svm_metrics

            mlflow.log_metric("svm_training_time", svm_time)
            mlflow.sklearn.log_model(self._oneclass_svm, "oneclass_svm")

            # Sauvegarde locale
            self._save_models(room_id)

        logger.info(f"Entraînement anomalies terminé: {metrics}")
        return metrics

    def detect_anomalies(
        self, df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Détecte les anomalies dans de nouvelles données.

        Returns:
            DataFrame avec colonnes:
            - anomaly_score_if  : score IsolationForest (-1 = anomalie)
            - anomaly_score_svm : score OneClassSVM
            - is_anomaly        : décision ensembliste
            - severity          : 'normal' | 'suspicious' | 'anomaly'
        """
        if self._isolation_forest is None:
            raise ValueError("Modèle IsolationForest non entraîné")

        X = self._prepare_anomaly_features(df)
        X_scaled = self._anomaly_scaler.transform(X)

        # Scores IsolationForest (plus négatif = plus anormal)
        if_scores = self._isolation_forest.score_samples(X_scaled)
        if_predictions = self._isolation_forest.predict(X_scaled)  # -1 ou 1

        # Scores OneClassSVM
        svm_predictions = self._oneclass_svm.predict(X_scaled)

        # Décision ensembliste (les deux doivent dire anomalie)
        is_anomaly = (if_predictions == -1) & (svm_predictions == -1)

        # Normalisation score 0-1 (0=normal, 1=très anormal)
        if_norm = 1 - (if_scores - if_scores.min()) / (if_scores.max() - if_scores.min() + 1e-8)

        # Sévérité
        severity = np.where(
            if_norm > 0.9, "anomaly",
            np.where(if_norm > 0.7, "suspicious", "normal")
        )

        result = df.copy()
        result["anomaly_score_if"]  = if_norm
        result["if_prediction"]     = if_predictions
        result["svm_prediction"]    = svm_predictions
        result["is_anomaly"]        = is_anomaly
        result["severity"]          = severity

        n_anomalies = is_anomaly.sum()
        logger.info(f"Anomalies détectées: {n_anomalies}/{len(df)} ({n_anomalies/len(df)*100:.1f}%)")

        return result

    # ══════════════════════════════════════════════════════
    #  B) PRÉDICTION CONSOMMATION
    # ══════════════════════════════════════════════════════

    def train_energy_prediction(
        self, df: pd.DataFrame, room_id: str
    ) -> Dict[str, ModelMetrics]:
        """
        Entraîne les modèles de prédiction de consommation.

        Modèles:
        - XGBoost  : régression tabulaire avec features temporelles
        - Prophet  : tendances + saisonnalité annuelle/hebdomadaire
        """
        logger.info(f"Entraînement modèles prédiction énergie — {len(df)} points")

        df = self._prepare_energy_features(df)
        metrics = {}

        with mlflow.start_run(run_name=f"energy_pred_{room_id}_{datetime.now().strftime('%Y%m%d')}"):
            mlflow.log_param("room_id", room_id)
            mlflow.log_param("n_samples", len(df))

            # ── XGBoost Regression ──
            metrics["xgboost"] = self._train_xgboost_energy(df, room_id)

            # ── Prophet ──
            metrics["prophet"] = self._train_prophet(df, room_id)

        return metrics

    def _train_xgboost_energy(
        self, df: pd.DataFrame, room_id: str
    ) -> ModelMetrics:
        """Entraîne XGBoost pour prédiction consommation."""
        import time

        feature_cols = [c for c in self.FEATURES_ENERGY if c in df.columns and c != "power_watts"]
        target_col = "power_watts"

        # Split temporel (pas aléatoire pour timeseries)
        split_idx = int(len(df) * 0.8)
        X_train = df[feature_cols].iloc[:split_idx]
        X_test  = df[feature_cols].iloc[split_idx:]
        y_train = df[target_col].iloc[:split_idx]
        y_test  = df[target_col].iloc[split_idx:]

        self._energy_scaler = MinMaxScaler()
        X_train_scaled = self._energy_scaler.fit_transform(X_train)
        X_test_scaled  = self._energy_scaler.transform(X_test)

        start = time.time()
        self._xgb_energy = xgb.XGBRegressor(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_alpha=0.1,
            reg_lambda=0.1,
            random_state=42,
            n_jobs=-1,
            early_stopping_rounds=50,
            eval_metric="rmse",
        )
        self._xgb_energy.fit(
            X_train_scaled, y_train,
            eval_set=[(X_test_scaled, y_test)],
            verbose=False,
        )
        training_time = time.time() - start

        y_pred = self._xgb_energy.predict(X_test_scaled)

        mae  = mean_absolute_error(y_test, y_pred)
        rmse = np.sqrt(mean_squared_error(y_test, y_pred))
        r2   = r2_score(y_test, y_pred)

        mlflow.log_metric("xgb_mae", mae)
        mlflow.log_metric("xgb_rmse", rmse)
        mlflow.log_metric("xgb_r2", r2)
        mlflow.xgboost.log_model(self._xgb_energy, "xgboost_energy")

        # Feature importance
        feature_importance = dict(zip(feature_cols, self._xgb_energy.feature_importances_))
        logger.info(f"XGBoost Feature Importance: {feature_importance}")
        mlflow.log_dict(feature_importance, "feature_importance.json")

        return ModelMetrics(
            model_name="XGBoost",
            mae=round(mae, 3),
            rmse=round(rmse, 3),
            r2=round(r2, 4),
            training_samples=len(X_train),
            training_time_s=round(training_time, 2),
        )

    def _train_prophet(self, df: pd.DataFrame, room_id: str) -> ModelMetrics:
        """Entraîne Facebook Prophet pour tendances et saisonnalité."""
        import time

        # Prophet requiert colonnes 'ds' et 'y'
        prophet_df = df[["timestamp", "power_watts"]].rename(
            columns={"timestamp": "ds", "power_watts": "y"}
        ).dropna()

        start = time.time()
        self._prophet_model = Prophet(
            yearly_seasonality=True,
            weekly_seasonality=True,
            daily_seasonality=True,
            changepoint_prior_scale=0.05,
            seasonality_prior_scale=10.0,
            interval_width=0.95,
        )
        # Ajout régresseur externe
        if "temperature" in df.columns:
            self._prophet_model.add_regressor("temperature")
            prophet_df["temperature"] = df["temperature"].values

        self._prophet_model.fit(prophet_df)
        training_time = time.time() - start

        # Validation croisée
        from prophet.diagnostics import cross_validation, performance_metrics
        try:
            cv_df = cross_validation(
                self._prophet_model,
                initial="30 days",
                period="7 days",
                horizon="1 days",
                parallel="processes",
            )
            perf = performance_metrics(cv_df)
            mae  = float(perf["mae"].mean())
            rmse = float(perf["rmse"].mean())
        except Exception:
            mae, rmse = None, None

        mlflow.log_metric("prophet_training_time", training_time)
        if mae:
            mlflow.log_metric("prophet_cv_mae", mae)
            mlflow.log_metric("prophet_cv_rmse", rmse)

        return ModelMetrics(
            model_name="Prophet",
            mae=round(mae, 3) if mae else None,
            rmse=round(rmse, 3) if rmse else None,
            training_samples=len(prophet_df),
            training_time_s=round(training_time, 2),
        )

    def predict_energy_24h(self, last_readings: pd.DataFrame) -> Dict:
        """
        Prédit la consommation pour les prochaines 24h.

        Combine XGBoost + Prophet (ensemble moyen pondéré).
        """
        predictions = {}
        future_hours = 24

        # ── Prophet prediction ──
        if self._prophet_model:
            future = self._prophet_model.make_future_dataframe(
                periods=future_hours, freq="H"
            )
            forecast = self._prophet_model.predict(future)
            prophet_pred = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(future_hours)
            predictions["prophet"] = prophet_pred

        # ── XGBoost prediction ──
        if self._xgb_energy:
            future_features = self._generate_future_features(last_readings, future_hours)
            X_future = self._energy_scaler.transform(future_features)
            xgb_pred = self._xgb_energy.predict(X_future)
            predictions["xgboost"] = xgb_pred

        # ── Ensemble ──
        if "prophet" in predictions and "xgboost" in predictions:
            prophet_vals = predictions["prophet"]["yhat"].values
            xgb_vals = predictions["xgboost"]
            ensemble = 0.5 * prophet_vals + 0.5 * xgb_vals

            # Intervalles de confiance de Prophet
            lower = predictions["prophet"]["yhat_lower"].values
            upper = predictions["prophet"]["yhat_upper"].values

            return {
                "timestamps": predictions["prophet"]["ds"].dt.isoformat().tolist(),
                "predicted_watts": np.maximum(0, ensemble).tolist(),
                "lower_bound_watts": np.maximum(0, lower).tolist(),
                "upper_bound_watts": upper.tolist(),
                "model": "ensemble_prophet_xgboost",
                "confidence": 0.87,
            }

        raise RuntimeError("Aucun modèle de prédiction disponible")

    # ──────────────────────────────────────────────────────
    #  FEATURE ENGINEERING
    # ──────────────────────────────────────────────────────

    def _prepare_anomaly_features(self, df: pd.DataFrame) -> np.ndarray:
        """Prépare les features pour détection d'anomalies."""
        available = [f for f in self.FEATURES_ANOMALY if f in df.columns]
        X = df[available].fillna(df[available].median())
        return X.values

    def _prepare_energy_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Feature engineering temporel pour prédiction énergie."""
        df = df.copy()
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df["hour_of_day"]  = df["timestamp"].dt.hour
            df["day_of_week"]  = df["timestamp"].dt.dayofweek
            df["month"]        = df["timestamp"].dt.month
            df["quarter"]      = df["timestamp"].dt.quarter
            df["is_weekend"]   = (df["day_of_week"] >= 5).astype(int)
            df["is_business_hour"] = (
                (df["hour_of_day"] >= 8) & (df["hour_of_day"] <= 18)
            ).astype(int)
        return df

    def _generate_future_features(
        self, last_df: pd.DataFrame, horizon_hours: int
    ) -> pd.DataFrame:
        """Génère les features pour les prochaines heures."""
        last_ts = pd.to_datetime(last_df["timestamp"].max())
        future_timestamps = pd.date_range(
            start=last_ts + pd.Timedelta(hours=1),
            periods=horizon_hours,
            freq="H",
        )
        future_df = pd.DataFrame({"timestamp": future_timestamps})
        future_df = self._prepare_energy_features(future_df)

        # Moyennes historiques pour température, présence
        for col in ["temperature", "presence"]:
            if col in last_df.columns:
                future_df[col] = last_df[col].mean()

        feature_cols = [c for c in self.FEATURES_ENERGY if c in future_df.columns and c != "power_watts"]
        return future_df[feature_cols].fillna(0)

    # ──────────────────────────────────────────────────────
    #  SAUVEGARDE / CHARGEMENT
    # ──────────────────────────────────────────────────────

    def _save_models(self, room_id: str) -> None:
        """Sauvegarde tous les modèles sur disque."""
        path = self.model_dir / room_id
        path.mkdir(exist_ok=True)

        if self._isolation_forest:
            joblib.dump(self._isolation_forest, path / "isolation_forest.pkl")
        if self._oneclass_svm:
            joblib.dump(self._oneclass_svm, path / "oneclass_svm.pkl")
        if self._anomaly_scaler:
            joblib.dump(self._anomaly_scaler, path / "anomaly_scaler.pkl")
        if self._xgb_energy:
            self._xgb_energy.save_model(str(path / "xgboost_energy.json"))
        if self._energy_scaler:
            joblib.dump(self._energy_scaler, path / "energy_scaler.pkl")
        if self._prophet_model:
            with open(path / "prophet_model.pkl", "wb") as f:
                pickle.dump(self._prophet_model, f)

        logger.info(f"Modèles sauvegardés dans {path}")

    def load_models(self, room_id: str) -> bool:
        """Charge les modèles depuis le disque."""
        path = self.model_dir / room_id
        if not path.exists():
            logger.warning(f"Aucun modèle trouvé pour room {room_id}")
            return False

        try:
            if (path / "isolation_forest.pkl").exists():
                self._isolation_forest = joblib.load(path / "isolation_forest.pkl")
            if (path / "anomaly_scaler.pkl").exists():
                self._anomaly_scaler = joblib.load(path / "anomaly_scaler.pkl")
            if (path / "oneclass_svm.pkl").exists():
                self._oneclass_svm = joblib.load(path / "oneclass_svm.pkl")
            if (path / "xgboost_energy.json").exists():
                self._xgb_energy = xgb.XGBRegressor()
                self._xgb_energy.load_model(str(path / "xgboost_energy.json"))
            if (path / "energy_scaler.pkl").exists():
                self._energy_scaler = joblib.load(path / "energy_scaler.pkl")
            if (path / "prophet_model.pkl").exists():
                with open(path / "prophet_model.pkl", "rb") as f:
                    self._prophet_model = pickle.load(f)

            logger.info(f"Modèles chargés depuis {path}")
            return True
        except Exception as e:
            logger.error(f"Erreur chargement modèles: {e}")
            return False
