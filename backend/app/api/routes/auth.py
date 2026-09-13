from fastapi import APIRouter, HTTPException, status, Depends
from app.db.session import get_db_connection
from app.core.security import hash_password, verify_password, create_access_token
from app.schemas.user import UserCreate, UserLogin, UserOut, Token
from app.api.deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM users WHERE email = %s", (payload.email,))
            if cursor.fetchone():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

            hashed = hash_password(payload.password)
            cursor.execute(
                "INSERT INTO users (email, hashed_password, full_name) VALUES (%s, %s, %s)",
                (payload.email, hashed, payload.full_name),
            )
            conn.commit()
            new_user_id = cursor.lastrowid

            cursor.execute("SELECT id, email, full_name, is_active FROM users WHERE id = %s", (new_user_id,))
            user_row = cursor.fetchone()
        return user_row
    finally:
        conn.close()


@router.post("/login", response_model=Token)
def login(payload: UserLogin):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM users WHERE email = %s", (payload.email,))
            user = cursor.fetchone()

        if not user or not verify_password(payload.password, user["hashed_password"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

        token = create_access_token(user["id"])
        return Token(access_token=token)
    finally:
        conn.close()


@router.get("/me", response_model=UserOut)
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user