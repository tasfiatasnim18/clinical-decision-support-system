import os
import json
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, text
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from jose import jwt, JWTError
from datetime import datetime, timedelta
from passlib.hash import bcrypt
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

# 1️⃣ Create FastAPI app first
app = FastAPI(title="MedAI Doctor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===================== DATABASE =====================
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600
)
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL not set")

# ===================== JWT SETTINGS =====================
SECRET_KEY = os.getenv("JWT_DOCTOR_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_DOCTOR_SECRET_KEY not set")

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ALGORITHM = JWT_ALGORITHM

ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 1440)
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ===================== SCHEMAS =====================
class DoctorRegister(BaseModel):
    username: str
    password: str
    full_name: str
    email: str
    specialization: str

# ===================== HELPER FUNCTION =====================
def safe_json(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/doctor/login")

def doctor_required(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "doctor":
            raise HTTPException(403, "Doctor access only")
        return payload
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
# ===================== REGISTER =====================
@app.post("/register")
def register(doctor: DoctorRegister):
    with engine.begin() as conn:
        if conn.execute(
            text("SELECT 1 FROM doctors WHERE username=:u"),
            {"u": doctor.username}
        ).first():
            raise HTTPException(400, "Username already exists")

        conn.execute(
            text("""
                INSERT INTO doctors
                (username, password, full_name, email, specialization, status, created_at)
                VALUES (:u, :p, :f, :e, :s, 'PENDING', NOW())
            """),
            {
                "u": doctor.username,
                "p": bcrypt.hash(doctor.password),
                "f": doctor.full_name,
                "e": doctor.email,
                "s": doctor.specialization
            }
        )
    return {"message": "Registration submitted for approval"}

# ===================== LOGIN =====================
@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    with engine.connect() as conn:
        doctor = conn.execute(
            text("SELECT * FROM doctors WHERE username=:u"), {"u": form_data.username}
        ).first()
        if not doctor or not bcrypt.verify(form_data.password, doctor.password):
            raise HTTPException(401, "Invalid credentials")
        if doctor.status != "APPROVED":
            raise HTTPException(403, "Account not approved")
        token_data = {
            "sub": doctor.username,
            "id": doctor.id,
            "role": "doctor",
            "exp": datetime.utcnow() + timedelta(hours=12)
        }

        token = jwt.encode(token_data, SECRET_KEY, algorithm=ALGORITHM)
        return {"access_token": token, "token_type": "bearer"}

# ===================== CURRENT DOCTOR =====================
@app.get("/me")
def get_me(payload=Depends(doctor_required)):
    username = payload["sub"]
    with engine.connect() as conn:
        doctor = conn.execute(
            text("""
                SELECT id, username, full_name, email, specialization
                FROM doctors WHERE username=:u
            """),
            {"u": username}
        ).first()

        if not doctor:
            raise HTTPException(404, "Doctor not found")
        return dict(doctor._mapping)

# ===================== PATIENT HISTORY =====================
@app.get("/patients/history")
def doctor_patient_history(
    q: str = Query(...),
    page: int = Query(1, ge=1),
    limit: int = Query(10, le=50),
    payload=Depends(doctor_required)
):
    offset = (page - 1) * limit

    with engine.begin() as conn:
        total = conn.execute(
            text("""
                SELECT COUNT(DISTINCT p.prescription_serial)
                FROM patient_details pd
                JOIN patient_health_records phr ON pd.patient_id = phr.patient_id
                JOIN prescriptions p ON p.prescription_serial = phr.prescription_serial
                WHERE pd.patient_id = :q OR pd.phone = :q
            """),
            {"q": q}
        ).scalar()

        rows = conn.execute(
            text("""
                SELECT
                    p.prescription_serial,
                    p.created_at,

                    pd.patient_id,
                    pd.name,
                    pd.phone,
                    pd.age,
                    pd.gender,

                    phr.height_cm,
                    phr.weight_kg,
                    phr.bmi,
                    phr.bp_systolic,
                    phr.bp_diastolic,
                    phr.symptoms,
                    phr.medicines,
                    phr.tests,

                    dp.obesity_prediction_result,
                    dp.obesity_confidence_score,
                    dp.obesity_risk_score,
                    dp.obesity_features_json,
        
                    dp.diabetes_prediction_result,
                    dp.diabetes_confidence_score,
                    dp.diabetes_risk_score,
                    dp.diabetes_features_json,
        
                    dp.liver_prediction_result,
                    dp.liver_confidence_score,
                    dp.liver_risk_score,
                    dp.liver_features_json,
        
                    dp.hypertension_prediction_result,
                    dp.hypertension_confidence_score,
                    dp.hypertension_risk_score,
                    dp.hypertension_features_json,

                    GREATEST(
                      COALESCE(dp.obesity_risk_score, 0),
                      COALESCE(dp.diabetes_risk_score, 0),
                      COALESCE(dp.liver_risk_score, 0),
                      COALESCE(dp.hypertension_risk_score, 0)
                    ) AS max_risk

                FROM patient_details pd
                JOIN patient_health_records phr
                  ON pd.patient_id = phr.patient_id
                JOIN prescriptions p
                  ON p.prescription_serial = phr.prescription_serial
                LEFT JOIN disease_prediction dp
                  ON dp.prescription_serial = p.prescription_serial

                WHERE pd.patient_id = :q OR pd.phone = :q
                ORDER BY p.created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"q": q, "limit": limit, "offset": offset}
        ).fetchall()


    # 3️⃣ RESPONSE ASSEMBLY
    visits = {}

    for r in rows:
        rx = r.prescription_serial

        if rx not in visits:
            visits[rx] = {
                "prescription_serial": rx,
                "created_at": r.created_at,

                "patient": {
                    "patient_id": r.patient_id,
                    "name": r.name,
                    "phone": r.phone,
                    "age": r.age,
                    "gender": r.gender,
                },

                "vitals": {
                    "height_cm": r.height_cm,
                    "weight_kg": r.weight_kg,
                    "bmi": r.bmi,
                    "bp": {
                        "systolic": r.bp_systolic,
                        "diastolic": r.bp_diastolic,
                    }
                },

                "clinical": {
                    "symptoms": r.symptoms,
                    "medicines": r.medicines,
                    "tests": r.tests,
                },

                "predictions": []
            }

        DISEASES = [
            ("obesity", "Obesity"),
            ("diabetes", "Diabetes"),
            ("liver", "Liver Disease"),
            ("hypertension", "Hypertension"),
        ]
        
        if "predictions" not in visits[rx]:
            visits[rx]["predictions"] = []
        
        existing = {p["disease"] for p in visits[rx]["predictions"]}
        
        for key, label in DISEASES:
            if key in existing:
                continue
        
            result = getattr(r, f"{key}_prediction_result")
            if result is None or result == -1:
                continue
        
            visits[rx]["predictions"].append({
                "disease": key,
                "label": label,
                "result": result,
                "confidence": float(getattr(r, f"{key}_confidence_score"))
                    if getattr(r, f"{key}_confidence_score") is not None else None,
                "risk": float(getattr(r, f"{key}_risk_score"))
                    if getattr(r, f"{key}_risk_score") is not None else None,
                "features_json": safe_json(
                    getattr(r, f"{key}_features_json")
                )
            })


        visits[rx]["predictions"].sort(
            key=lambda x: x["risk"] if x["risk"] is not None else -1,
            reverse=True
        )

        visits[rx]["summary"] = {
            "diseases_detected": len(visits[rx]["predictions"]),
            "has_high_risk": any(
                p["risk"] is not None and p["risk"] >= 70
                for p in visits[rx]["predictions"]
            ),
            "max_risk": float(r.max_risk)
        }

    # 4️⃣ FINAL PAGINATED RESPONSE
    return {
        "page": page,
        "limit": limit,
        "total_records": total,
        "total_pages": (total + limit - 1) // limit,
        "has_next": page < ((total + limit - 1) // limit),
        "has_prev": page > 1,
        "data": list(visits.values())
    }
