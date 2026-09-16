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
CREATE_EMAILS_TABLE = """
CREATE TABLE IF NOT EXISTS emails (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    gmail_message_id VARCHAR(255) NOT NULL,
    thread_id VARCHAR(255) NOT NULL,
    direction ENUM('incoming', 'outgoing') NOT NULL,
    sender_email VARCHAR(255),
    recipient_email VARCHAR(255),
    subject TEXT,
    snippet TEXT,
    sent_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE KEY unique_message_per_user (user_id, gmail_message_id)
);
"""
CREATE_REPLY_PAIRS_TABLE = """
CREATE TABLE IF NOT EXISTS reply_pairs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    thread_id VARCHAR(255) NOT NULL,
    incoming_email_id INT NOT NULL,
    outgoing_email_id INT NOT NULL,
    reply_delay_minutes INT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (incoming_email_id) REFERENCES emails(id) ON DELETE CASCADE,
    FOREIGN KEY (outgoing_email_id) REFERENCES emails(id) ON DELETE CASCADE,
    UNIQUE KEY unique_pair (incoming_email_id, outgoing_email_id)
);
"""

ALTER_EMAILS_ADD_URGENCY = """
ALTER TABLE emails ADD COLUMN is_urgent BOOLEAN DEFAULT FALSE;
"""

ALTER_EMAILS_ADD_KEYWORDS = """
ALTER TABLE emails ADD COLUMN matched_keywords VARCHAR(500);
"""

ALTER_EMAILS_ADD_DEADLINE = """
ALTER TABLE emails ADD COLUMN deadline_at DATETIME NULL;
"""
def main():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(CREATE_USERS_TABLE)
            cursor.execute(CREATE_GMAIL_CONNECTIONS_TABLE)
            cursor.execute(CREATE_EMAILS_TABLE)
            cursor.execute(CREATE_REPLY_PAIRS_TABLE)
            for alter_stmt in [ALTER_EMAILS_ADD_URGENCY, ALTER_EMAILS_ADD_KEYWORDS, ALTER_EMAILS_ADD_DEADLINE]:
                try:
                    cursor.execute(alter_stmt)
                except Exception as e:
                    if "Duplicate column name" not in str(e):
                        raise

        conn.commit()
        print("users table created successfully.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()