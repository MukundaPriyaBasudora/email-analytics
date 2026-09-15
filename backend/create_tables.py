from app.db.session import get_db_connection

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE
);
"""
CREATE_GMAIL_CONNECTIONS_TABLE = """
CREATE TABLE IF NOT EXISTS gmail_connections (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL UNIQUE,
    google_email VARCHAR(255),
    encrypted_refresh_token TEXT,
    is_connected BOOLEAN DEFAULT FALSE,
    connected_at DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

def main():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_USERS_TABLE)
            cursor.execute(CREATE_GMAIL_CONNECTIONS_TABLE)

        conn.commit()
        print("users table created successfully.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()