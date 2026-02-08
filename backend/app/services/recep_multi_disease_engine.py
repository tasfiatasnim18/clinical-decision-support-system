#!/usr/bin/env python
# coding: utf-8

# In[ ]:


import numpy as np
import joblib

# ==================================================
# LOAD MODELS
# ==================================================
MODELS = {
    "obesity": {
        "model": joblib.load(
            r"D:/E/Frontend/models/obesity/final_tuned_xgboost_model.pkl"
        ),
        "scaler": joblib.load(
            r"D:/E/Frontend/models/obesity/scaler.pkl"
        ),
        "features": ["age", "gender", "height_cm", "weight_kg", "bmi"]
    },

    "liver": {
        "model": joblib.load(
            r"D:/E/Frontend/models/liver/cld_xgboost_model.pkl"
        ),
        "scaler": joblib.load(
            r"D:/E/Frontend/models/liver/cld_scaler.pkl"
        ),
        "features": [
            "age_of_the_patient",
            "gender_of_the_patient",
            "total_bilirubin",
            "direct_bilirubin",
            "alkphos_alkaline_phosphotase",
            "sgpt_alamine_aminotransferase",
            "sgot_aspartate_aminotransferase",
            "total_protiens",
            "alb_albumin",
            "a/g_ratio_albumin_and_globulin_ratio"
        ]
    },

    "cardiovascular": {
        "model": joblib.load(
            r"D:/E/Frontend/models/hypertension/hypertension_xgboost_model.pkl"
        ),
        "scaler": joblib.load(
            r"D:/E/Frontend/models/hypertension/hypertension_scaler.pkl"
        ),
        "features": [
            "age",
            "gender",
            "height_cm",
            "weight_kg",
            "ap_hi",
            "ap_lo",
            "cholesterol",
            "gluc",
            "smoke",
            "alco",
            "active",
            "bmi",
            "pulse_pressure",
            "map"
        ]
    },

    "diabetes": {
        "model": joblib.load(
            r"D:/E/Frontend/models/diabetes/diabetes_model_rf_smote.pkl"
        ),
        "features": [
            "pregnancies",
            "glucose",
            "ap_lo",
            "skin_thickness",
            "insulin",
            "bmi",
            "dpf",
            "age"
        ]
    }
}

# ==================================================
# FEATURE AVAILABILITY CHECK
# ==================================================
def has_required_features(features, data):
    return all(data.get(f) is not None for f in features)

# ==================================================
# BEST REALISTIC FUTURE RISK (RULE-BASED)
# ==================================================
def calculate_future_risk(data, disease=None, prediction=None):
    risk = 0

    def v(key):
        val = data.get(key)
        return val if isinstance(val, (int, float)) else 0

    # AGE
    age = v("age")
    if age >= 60: risk += 25
    elif age >= 45: risk += 18
    elif age >= 30: risk += 10

    # BMI
    bmi = v("bmi")
    if bmi >= 35: risk += 25
    elif bmi >= 30: risk += 18
    elif bmi >= 25: risk += 10

    # GLUCOSE
    glucose = v("glucose")
    if glucose >= 140: risk += 25
    elif glucose >= 126: risk += 18
    elif glucose >= 100: risk += 10

    # DIASTOLIC BP
    dbp = v("ap_lo")
    if dbp >= 100: risk += 25
    elif dbp >= 90: risk += 18
    elif dbp >= 85: risk += 10

    # 🔴 KEY FIX: disease baseline
    if prediction == 1:
        if disease == "diabetes":
            risk = max(risk, 40)
        elif disease == "cardiovascular":
            risk = max(risk, 35)
        elif disease == "liver":
            risk = max(risk, 30)
        elif disease == "obesity":
            risk = max(risk, 25)

    return min(risk, 100)

# ==================================================
# RUN SINGLE MODEL
# ==================================================
def run_model(cfg, data, disease_name):
    values = [data[f] for f in cfg["features"]]
    X = np.array([values])

    if "scaler" in cfg:
        X = cfg["scaler"].transform(X)

    model = cfg["model"]

    # Prediction
    pred = int(model.predict(X)[0])

    # Confidence (safe)
    if hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)[0]
        confidence = float(np.max(proba)) * 100
    else:
        confidence = 100.0  # fallback

    # ----------------------------
    # 🔴 KEY PART: features_used
    # ----------------------------
    features_used = {
        f: data.get(f)
        for f in cfg["features"]
        if data.get(f) is not None
    }

    return {
        "prediction": pred,
        "confidence": round(confidence, 2),
        "future_risk": calculate_future_risk(
            data,
            disease=disease_name,
            prediction=pred
        ),
        "features_used": features_used 
    }


# ==================================================
# AUTO MULTI-DISEASE ENGINE
# ==================================================
def multi_disease_screening(data):
    results = {}

    for name, cfg in MODELS.items():
        if has_required_features(cfg["features"], data):
            results[name] = run_model(cfg, data, name)
        else:
            results[name] = {
                "prediction": -1,
                "confidence": 0,
                "future_risk": 0
            }

    return results

DISEASE_FEATURE_MAP = {
    name: cfg["features"]
    for name, cfg in MODELS.items()
}

DISEASE_MAP = {
    "obesity": 1,
    "diabetes": 2,
    "liver": 3,
    "cardiovascular": 4
}
