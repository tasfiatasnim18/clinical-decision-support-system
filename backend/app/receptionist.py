import io
import re
import os
import warnings
import numpy as np
import json
import joblib
import smtplib
from pathlib import Path
from datetime import datetime, timedelta
from email.message import EmailMessage
from dotenv import load_dotenv

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, text
from passlib.context import CryptContext
from jose import jwt, JWTError
from pydantic import BaseModel, EmailStr
from google.cloud import vision
from transformers import AutoTokenizer, AutoModelForTokenClassification, pipeline as hf_pipeline
from services.recep_multi_disease_engine import (multi_disease_screening, DISEASE_FEATURE_MAP)

warnings.filterwarnings("ignore", category=FutureWarning)

load_dotenv()

router = APIRouter(prefix="/api/receptionist", tags=["Receptionist"])

# -----------------------------
# 1. CONFIGURATION
# -----------------------------
BASE_DIR = Path(r"D:/E/Frontend")
MODEL_PATH = BASE_DIR / "models" / "english_ner"
GOOGLE_CREDS = BASE_DIR / "GoogleCloudAPI" / "handwritingocr-481216-593bc8379de9.json"

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
SECRET_KEY = os.getenv("JWT_RECEPTIONIST_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_RECEPTIONIST_SECRET_KEY not set")

JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ALGORITHM = JWT_ALGORITHM

ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 1440)
)
    
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login")

# -----------------------------
# 2. SCHEMAS
# -----------------------------
class ReceptionistRegister(BaseModel):
    username: str
    name: str
    email: EmailStr
    password: str

class ProfileUpdate(BaseModel):
    name: str
    email: EmailStr

# -----------------------------
# 3. MODELS & SERVICES INITIALIZATION
# -----------------------------
try:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(GOOGLE_CREDS)
    vision_client = vision.ImageAnnotatorClient()
    
    tokenizer = AutoTokenizer.from_pretrained(str(MODEL_PATH))
    ner_model = AutoModelForTokenClassification.from_pretrained(str(MODEL_PATH))
    ner_pipeline = hf_pipeline("ner", model=ner_model, tokenizer=tokenizer, aggregation_strategy="first")
    
    print("✅ System initialized successfully.")
except Exception as e:
    print(f"❌ Initialization Error: {e}")

# -----------------------------
# GLOBAL CACHED DISEASE MAP
# -----------------------------
try:
    with engine.begin() as conn:
        DISEASE_MAP = {
            r.disease_name: r.id
            for r in conn.execute(
                text("SELECT id, disease_name FROM diseases")
            )
        }
    print("✅ Disease map cached successfully.")
except Exception as e:
    DISEASE_MAP = {}
    print("❌ Failed to cache disease map:", e)

# -----------------------------
# 4. CONSTANTS & MAPS
# -----------------------------
NER_LABEL_MAP = {
    "PROBLEM": "symptoms",
    "SIGN": "symptoms",
    "DISEASE": "symptoms",
    "DRUG": "medicines",
    "TREATMENT": "medicines",
    "TEST": "tests",
    "LAB": "tests",
}

# -----------------------------
# 5. UTILITY FUNCTIONS
# -----------------------------

def safe_join(values):
    return ", ".join(sorted(values)) if values else ""

def to_python(obj):
    if isinstance(obj, np.generic): return obj.item()
    return obj

def simple_ner_extract(text: str):
    results = {
        "symptoms": set(),
        "medicines": set(),
        "tests": set()
    }

    for ent in ner_pipeline(text):
        if ent.get("score", 0) < 0.6:
            continue

        category = NER_LABEL_MAP.get(ent.get("entity_group", "").upper())
        if not category:
            continue

        raw = ent["word"]

        # 🔹 FIX: split camelCase + bad OCR merges
        raw = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
        raw = re.sub(r"[^a-zA-Z\s]", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip().lower()

        for token in raw.split():
            if len(token) < 3:
                continue
            results[category].add(token)

    return {k: ", ".join(sorted(v)) for k, v in results.items()}

# --------------------------------------------------
# HELPERS
# --------------------------------------------------
def num(pattern, text, cast=float):
    m = re.search(pattern, text, re.I)
    return cast(m.group(1)) if m else None
    
# --------------------------------------------------
# FEATURE EXTRACTION (SINGLE SOURCE OF TRUTH)
# --------------------------------------------------
def extract_vitals(text: str):
    # =========================
    # BASE DICTIONARY
    # =========================
    d = {
        # OBESITY FEATURES
        "age": num(r"Age[:\- ]*(\d+)", text, int),
        "gender": None,
        "height_cm": num(r"Height[:\- ]*(\d+\.?\d*)", text),
        "weight_kg": num(r"Weight[:\- ]*(\d+\.?\d*)", text),
        "bmi": None,

        # LIVER FEATURES (INITIAL)
        "age_of_the_patient": num(r"Age[:\- ]*(\d+)", text, int),
        "gender_of_the_patient": None,
        "total_bilirubin": None,
        "direct_bilirubin": None,
        "alkphos_alkaline_phosphotase": None,
        "sgpt_alamine_aminotransferase": None,
        "sgot_aspartate_aminotransferase": None,
        "total_protiens": None,
        "alb_albumin": None,
        "a/g_ratio_albumin_and_globulin_ratio": None,

        # HYPERTENSION FEATURES
        "ap_hi": None,
        "ap_lo": None,
        "cholesterol": None,
        "gluc": None,
        "smoke": None,
        "alco": None,
        "active": None,
        "pulse_pressure": None,
        "map": None,

        # DIABETES FEATURES
        "pregnancies": None,
        "skin_thickness": None,
        "insulin": None,
        "dpf": None,

    }

    # =========================
    # LIVER FEATURE EXTRACTION (FIXED REGEX)
    # =========================
    d.update({
        "total_bilirubin": num(
            r"Total Bilirubin[:\- ]*(\d+\.?\d*)", text
        ),

        "direct_bilirubin": num(
            r"Direct Bilirubin[:\- ]*(\d+\.?\d*)", text
        ),

        "alkphos_alkaline_phosphotase": num(
            r"(?:ALP|Alkaline Phosphatase)[:\- ]*(\d+\.?\d*)",
            text
        ),

        "sgpt_alamine_aminotransferase": num(
            r"SGPT(?:\s*\(ALT\))?[:\- ]*(\d+\.?\d*)",
            text
        ),

        "sgot_aspartate_aminotransferase": num(
            r"SGOT(?:\s*\(AST\))?[:\- ]*(\d+\.?\d*)",
            text
        ),

        "total_protiens": num(
            r"(?:Total Protein|Total Proteins)[:\- ]*(\d+\.?\d*)",
            text
        ),

        "alb_albumin": num(
            r"(?:Albumin|ALB)[:\- ]*(\d+\.?\d*)",
            text
        ),

        "a/g_ratio_albumin_and_globulin_ratio": num(
            r"(?:A/G Ratio|Albumin/Globulin Ratio)[:\- ]*(\d+\.?\d*)",
            text
        ),
    })

    # =========================
    # HYPERTENTION FEATURE EXTRACTION (FIXED REGEX)
    # =========================
    d.update({
        "cholesterol": num(r"Cholesterol[:\- ]*(\d+)", text, int),
        "gluc": num(r"Glucose[:\- ]*(\d+)", text, int),
        "smoke": num(r"(?:Smoking|Smoke)[:\- ]*(\d+)", text, int),
        "alco": num(r"Alcohol[:\- ]*(\d+)", text, int),
        "active": num(r"(?:Physical Activity|Active)[:\- ]*(\d+)", text, int),
    })

    # Blood Pressure (BP or AP High / AP Low)
    bp = re.search(r"BP[:\- ]*(\d{2,3})\s*/\s*(\d{2,3})", text)
    ap = re.search(r"AP High[:\- ]*(\d{2,3}).*?AP Low[:\- ]*(\d{2,3})", text, re.I)
    
    if bp:
        d["ap_hi"] = int(bp.group(1))
        d["ap_lo"] = int(bp.group(2))
    elif ap:
        d["ap_hi"] = int(ap.group(1))
        d["ap_lo"] = int(ap.group(2))

    # Pulse Pressure & MAP (direct from OCR if available)
    d["pulse_pressure"] = num(r"Pulse Pressure[:\- ]*(\d+\.?\d*)", text)
    d["map"] = num(r"MAP[:\- ]*(\d+\.?\d*)", text)

    # Pulse Pressure & MAP
    if d["pulse_pressure"] is None and d["ap_hi"] is not None and d["ap_lo"] is not None:
        d["pulse_pressure"] = d["ap_hi"] - d["ap_lo"]
    
    if d["map"] is None and d["ap_hi"] is not None and d["ap_lo"] is not None:
        d["map"] = round(d["ap_lo"] + (d["pulse_pressure"] / 3), 2)

    # =========================
    # DIABETES FEATURE EXTRACTION
    # =========================
    d.update({
        "pregnancies": num(r"Pregnancies[:\- ]*(\d+)", text, int),
    
        "skin_thickness": num(
            r"(?:Skin Thickness|SkinFold)[:\- ]*(\d+\.?\d*)",
            text
        ),
    
        "insulin": num(
            r"Insulin[:\- ]*(\d+\.?\d*)",
            text
        ),
    
        "dpf": num(
            r"(?:DPF|Diabetes Pedigree Function)[:\- ]*(\d+\.?\d*)",
            text
        ),
    })

    # Map glucose (for diabetes)
    if d["gluc"] is not None:
        d["glucose"] = d["gluc"]
    else:
        d["glucose"] = None


    # =========================
    # GENDER (MATCHES KAGGLE)
    # =========================
    g = re.search(r"\b(Male|Female)\b", text, re.I)
    if g:
        gender_val = 0 if g.group(1).lower() == "male" else 1
        d["gender"] = gender_val
        d["gender_of_the_patient"] = gender_val

    # =========================
    # BMI CALCULATION
    # =========================
    if d["height_cm"] and d["weight_kg"]:
        d["bmi"] = round(
            d["weight_kg"] / ((d["height_cm"] / 100) ** 2),
            2
        )
        
    # =========================
    # UNIFIED AGE (FOR RISK CALCULATION)
    # =========================
    if d["age"] is None and d["age_of_the_patient"] is not None:
        d["age"] = d["age_of_the_patient"]
        
    return d


# =========================
# PATIENT DETAILS
# =========================
def extract_prescription_serial(text: str):
    patterns = [
        r"(?:prescription\s*serial|rx\s*no|prescription\s*no)\s*[:#\-]?\s*(\d{3,})",
        r"(?:serial\s*no)\s*[:#\-]?\s*(\d{3,})"
    ]

    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1)

    return None

def extract_patient_identity(text_data: str):
    details = {}

    # Patient ID
    pid_m = re.search(
        r"(?:Patient\s*ID|Pt\s*ID|PID|ID)\s*[:\-]?\s*([A-Za-z0-9\-\/]+)",
        text_data, re.I
    )
    if pid_m:
        details["patient_id"] = pid_m.group(1).strip()

    # Patient Name (FIXED)
    name_m = re.search(
        r"(?:Patient\s*Name|Pt\s*Name|Name)\s*[:\-]?\s*([A-Za-z.\s]{3,60})"
        r"(?=\s*(Contact|Phone|Mobile|Gender|Age|Wt|Weight|Ht|Height|BP|Blood|$))",
        text_data, re.I
    )
    if name_m:
        details["name"] = name_m.group(1).strip()

    # Phone
    phone_m = re.search(
        r"(?:Contact|Phone|Mobile|Tel)?\s*[:\-]?\s*(\+?8801\d{9}|01\d{9})",
        text_data
    )
    if phone_m:
        details["phone"] = phone_m.group(1).strip()

    return details



def extract_disease_features(vitals: dict, disease: str):
    feature_list = DISEASE_FEATURE_MAP.get(disease, [])
    return {
        f: vitals.get(f)
        for f in feature_list
        if vitals.get(f) is not None
    }

def hash_pass(p): return pwd_context.hash(p)
def verify_pass(p, h): return pwd_context.verify(p, h)

def create_token(data):
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**data, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

def receptionist_required(token: str = Depends(oauth2_scheme)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("role") != "receptionist":
            raise HTTPException(403, "Receptionist access only")
        return payload
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

# -----------------------------
# 6. API ENDPOINTS
# -----------------------------
@router.post("/analyze_and_store")
async def analyze_and_store(
    file: UploadFile = File(...),
    payload=Depends(receptionist_required)
):
    # --- FILE VALIDATION ---
    if file.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(400, "Only PNG or JPEG images are allowed")
    
    image_bytes = await file.read()
    
    if len(image_bytes) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image size exceeds 5MB limit")

    try:
        vision_image = vision.Image(content=image_bytes)
        response = vision_client.document_text_detection(image=vision_image)

        if not response.full_text_annotation.text:
            raise HTTPException(
                status_code=400,
                detail="Unable to extract readable text from prescription image"
            )


        clean_text = re.sub(
            r"\s+", " ",
            response.full_text_annotation.text
        ).strip()

        # ---------- Extraction ----------
        prescription_serial = extract_prescription_serial(clean_text)

        if not prescription_serial:
            raise HTTPException(
                400,
                "Prescription serial number not found in document"
            )

        vitals = extract_vitals(clean_text)

        try:
            ner_sections = simple_ner_extract(clean_text)
        except (RuntimeError, ValueError, KeyError) as e:
            print("NER failed:", e)
            ner_sections = {
                "symptoms": "",
                "medicines": "",
                "tests": ""
            }
        
        identity = extract_patient_identity(clean_text)

        patient_id = identity.get("patient_id") or f"PID-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        patient_name = identity.get("name") or "Unknown"
        phone_number = identity.get("phone")
        
        if not phone_number:
            raise HTTPException(400, "Patient phone number not found in document")
        
        vitals.update({
            "patient_id": patient_id,
            "name": patient_name,
            "phone": phone_number
        })

        # ---------- Disease Prediction ----------

        # 1️⃣ Run models
        diseases = multi_disease_screening(vitals)
        
        # 2️⃣ Base row (MUST come first)
        row = {
            "prescription_serial": prescription_serial,
            "patient_id": patient_id,
            "phone": phone_number,
            "model_name": "auto_ml_engine",
            "model_version": "v1.0",
        }
        
        # 3️⃣ Initialize ALL disease columns as NULL (CRITICAL)
        ALL_DISEASES = ["obesity", "diabetes", "liver", "hypertension"]
        
        for d in ALL_DISEASES:
            row[f"{d}_prediction_result"] = None
            row[f"{d}_confidence_score"] = None
            row[f"{d}_risk_score"] = None
            row[f"{d}_features_json"] = None
        
        # 4️⃣ Fill only valid predictions
        disease_ids = []
        disease_names = []
        
        for disease, result in diseases.items():
        
            # 🚫 skip insufficient data
            if result["prediction"] == -1:
                continue
        
            disease_id = DISEASE_MAP.get(disease)
            if not disease_id:
                continue
        
            disease_ids.append(str(disease_id))
            disease_names.append(disease)
        
            row[f"{disease}_prediction_result"] = result["prediction"]
            row[f"{disease}_confidence_score"] = result["confidence"]
            row[f"{disease}_risk_score"] = result["future_risk"]
            row[f"{disease}_features_json"] = json.dumps({
                "features": extract_disease_features(vitals, disease),
                "expected_features": DISEASE_FEATURE_MAP.get(disease, [])
            })
        
        # 5️⃣ Final disease id/name columns
        row["disease_ids"] = ",".join(disease_ids) if disease_ids else None
        row["disease_names"] = ",".join(disease_names) if disease_names else None

        # ---------- DB ----------        
        with engine.begin() as conn:

            # 0️⃣ Ensure patient exists
            conn.execute(
                text("""
                    INSERT INTO patient_details (
                        patient_id,
                        name,
                        phone,
                        age,
                        gender,
                        email,
                        created_at
                    )
                    VALUES (
                        :patient_id,
                        :name,
                        :phone,
                        :age,
                        :gender,
                        :email,
                        NOW()
                    )
                    ON DUPLICATE KEY UPDATE
                        name = VALUES(name),
                        phone = VALUES(phone),
                        age = VALUES(age),
                        gender = VALUES(gender),
                        email = VALUES(email);
                """),
                {
                    "patient_id": patient_id,
                    "name": patient_name,
                    "phone": phone_number,
                    "age": vitals.get("age"),
                    "gender": vitals.get("gender"),
                    "email": None
                },
            )
        
            # 🔐 1️⃣ DUPLICATE PRESCRIPTION CHECK
            exists = conn.execute(
                text("SELECT 1 FROM prescriptions WHERE prescription_serial = :s"),
                {"s": prescription_serial}
            ).first()
        
            if exists:
                raise HTTPException(409, "This prescription has already been uploaded")
        
            # 2️⃣ Insert prescription
            conn.execute(
                text("""
                    INSERT INTO prescriptions (
                        prescription_serial,
                        patient_id,
                        clean_text,
                        symptoms,
                        medicines,
                        tests,
                        created_at
                    )
                    VALUES (
                        :serial,
                        :pid,
                        :txt,
                        :sym,
                        :med,
                        :tst,
                        NOW()
                    )
                """),
                {
                    "serial": prescription_serial,
                    "pid": patient_id,
                    "txt": clean_text,
                    "sym": ner_sections["symptoms"],
                    "med": ner_sections["medicines"],
                    "tst": ner_sections["tests"],
                }
            )
        
            # 3️⃣ patient_health_records (VISIT LEVEL)
            conn.execute(
                text("""
                    INSERT INTO patient_health_records (
                        prescription_serial,
                        patient_id,
                        phone,
                        clean_text,
                        symptoms,
                        medicines,
                        tests,
                        height_cm,
                        weight_kg,
                        bmi,
                        bp_systolic,
                        bp_diastolic,
                        created_at
                    )
                    VALUES (
                        :serial,
                        :pid,
                        :phone,
                        :txt,
                        :sym,
                        :med,
                        :tst,
                        :height_cm,
                        :weight_kg,
                        :bmi,
                        :bp_sys,
                        :bp_dia,
                        NOW()
                    )
                """),
                {
                    "serial": prescription_serial,
                    "pid": patient_id,
                    "phone": phone_number,
                    "txt": clean_text,
                    "sym": ner_sections["symptoms"],
                    "med": ner_sections["medicines"],
                    "tst": ner_sections["tests"],
                    "height_cm": vitals.get("height_cm"),
                    "weight_kg": vitals.get("weight_kg"),
                    "bmi": vitals.get("bmi"),
                    "bp_sys": vitals.get("ap_hi"),
                    "bp_dia": vitals.get("ap_lo"),
                }
            )

            # disease_predictions
            conn.execute(
                text("""
                    INSERT INTO disease_prediction (
                        prescription_serial,
                        patient_id,
                        phone,
                        disease_ids,
                        disease_names,
                
                        obesity_prediction_result,
                        obesity_confidence_score,
                        obesity_risk_score,
                        obesity_features_json,
                
                        diabetes_prediction_result,
                        diabetes_confidence_score,
                        diabetes_risk_score,
                        diabetes_features_json,
                
                        liver_prediction_result,
                        liver_confidence_score,
                        liver_risk_score,
                        liver_features_json,
                
                        hypertension_prediction_result,
                        hypertension_confidence_score,
                        hypertension_risk_score,
                        hypertension_features_json,
                
                        model_name,
                        model_version,
                        created_at
                    )
                    VALUES (
                        :prescription_serial,
                        :patient_id,
                        :phone,
                        :disease_ids,
                        :disease_names,
            
                        :obesity_prediction_result,
                        :obesity_confidence_score,
                        :obesity_risk_score,
                        :obesity_features_json,
                
                        :diabetes_prediction_result,
                        :diabetes_confidence_score,
                        :diabetes_risk_score,
                        :diabetes_features_json,
                
                        :liver_prediction_result,
                        :liver_confidence_score,
                        :liver_risk_score,
                        :liver_features_json,
                
                        :hypertension_prediction_result,
                        :hypertension_confidence_score,
                        :hypertension_risk_score,
                        :hypertension_features_json,
                
                        :model_name,
                        :model_version,
                        NOW()
                    )
                """),
                row
            )
        

        return {
            "prescription_serial": prescription_serial,
            "patient_id": patient_id,
            "patient_identity": {
                "name": patient_name,
                "phone": phone_number
            },
            "clean_text": clean_text,
            "extracted_data": vitals,
            "diseases": diseases,
            "ner_extracted": ner_sections
        }

    except HTTPException:
        # business errors (400 / 409 / 401)
        raise

    except Exception:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail="Internal server error during prescription analysis"
        )

@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    with engine.begin() as conn:
        user = conn.execute(text("SELECT * FROM receptionists WHERE username=:u"), {"u": form_data.username}).first()
        if not user or not verify_pass(form_data.password, user.password_hash): raise HTTPException(401, "Invalid credentials")
        if user.status != "APPROVED": return {"status": user.status, "message": "Pending Admin Approval"}
        return {"access_token": create_token({"sub": user.username, "role": "receptionist"}), "token_type": "bearer", "username": user.username}

@router.get("/me")
def me(payload=Depends(receptionist_required)):
    with engine.begin() as conn:
        r = conn.execute(text("SELECT username, name, email, status FROM receptionists WHERE username=:u"), {"u": payload.get("sub")}).first()
        if not r: raise HTTPException(404, "Not found")
        return dict(r._mapping)

@router.put("/update_profile")
def update_profile(data: ProfileUpdate, payload=Depends(receptionist_required)):
    with engine.begin() as conn:
        conn.execute(text("UPDATE receptionists SET name=:n, email=:e WHERE username=:u"), {"n": data.name, "e": data.email, "u": payload.get("sub")})
    return {"message": "Updated"}

@router.post("/register")
def register(data: ReceptionistRegister):
    with engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM receptionists WHERE username=:u OR email=:e"), {"u": data.username, "e": data.email}).first()
        if exists: raise HTTPException(400, "User already exists")
        conn.execute(text("INSERT INTO receptionists (username, name, email, password_hash, status, created_at) VALUES (:u, :n, :e, :p, 'PENDING', NOW())"),
            {"u": data.username, "n": data.name, "e": data.email, "p": hash_pass(data.password)})
    return {"message": "Success"}