
from fastapi import FastAPI
from app.api.routes import auth,gmail


app = FastAPI(title="Email Analytics")

app.include_router(auth.router)
app.include_router(gmail.router)

@app.get("/api/health")
def health_check():
    return {"status": "ok"}