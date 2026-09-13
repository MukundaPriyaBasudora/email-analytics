import pymysql
from pymysql.cursors import DictCursor
from app.core.config import settings
def get_db_connection():
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        cursorclass=DictCursor,
        autocommit=False,
    )