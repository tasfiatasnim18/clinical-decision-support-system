# clinical-decision-support-system

An intelligent and scalable clinical decision support system for automated
prescription analysis and multi-disease risk prediction using OCR,
Clinical Named Entity Recognition (NER), and Machine Learning.

This project is developed as a final-year academic thesis, focusing on
improving healthcare workflows through intelligent automation and
data-driven clinical decision support.


## Key Features
- Automated prescription text extraction using Optical Character Recognition (OCR)
- Clinical Named Entity Recognition (NER) for medicine, symptom, and disease detection
- Multi-disease risk prediction using machine learning models
- Role-based access control for Doctor, Receptionist, Patient, and Admin
- Secure RESTful APIs with JWT-based authentication
- Scalable and modular backend architecture


## Technology Stack

### Frontend
- React
- Axios
- Framer Motion

### Backend
- FastAPI (Python)
- SQLAlchemy

### Database
- MySQL

### AI / Machine Learning
- OCR (Tesseract)
- Clinical Named Entity Recognition (NER)
- Machine Learning (Scikit-learn)

### Other Tools
- REST API
- JWT Authentication
- Role-Based Access Control (RBAC)


## Project Structure
```txt
clinical-decision-support-system/
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ home.py/
│  │  ├─ admin.py/
│  │  ├─ receptionist.py/
│  │  ├─ doctor.py/
│  │  ├─ patient.py/
│  │  ├─ services/
│  ├─ requirements.txt
│  ├─ .env.example
│
├─ frontend/
│  ├─ src/
│  │  ├─ components/
│  │  ├─ pages/
├─ .gitignore
```

## How to Run the Project (Local Environment)
Backend Setup
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

Frontend Setup
cd frontend
npm install
npm run dev

## Environment Variables
1. Create a .env file inside the backend directory
2. Use .env.example as a reference
3. Do NOT commit the .env file to version control

## Project Status
1. Currently running in a local development environment
2. Architected to support future cloud deployment
3. Suitable for academic, research, and prototype use

## Future Improvements
1. Cloud deployment using Docker and cloud platforms
2. Integration with Electronic Health Record (EHR) systems
3. Advanced deep learning models for clinical risk prediction
4. Real-time clinical alert and recommendation system

## License
This project is licensed under the MIT License.

## Author
Tasfia Tasnim
Final Year Thesis Project
Department of Computer Science and Engineering
