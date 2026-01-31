from abc import ABC, abstractmethod
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Any, Dict

import logging

class AbstractDBHandler(ABC):
    def __init__(self, host: str = 'localhost', port: int = 5433, database: str = "emotions", 
                 user: str = "app_user", password: str = "basic_app_password"):
        self._connection_params = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password
        }

    def connect(self):
        try:
            self._conn = psycopg2.connect(**self._connection_params)
            self._cursor = self._conn.cursor(cursor_factory=RealDictCursor)
            logging.info(f"Connected to database")
        except Exception as e:
            logging.error(f"Error during connecting to Postgres: {e}")
            raise

    def close(self):
        if hasattr(self, '_cursor'):
            self._cursor.close()
        if hasattr(self, '_conn'):
            self._conn.close()
        logging.info("Connection closed")
    
    @abstractmethod
    def insert(self, args: Any):
        raise NotImplementedError("Abstract class method called")

    @abstractmethod
    def fetch(self, args: Any):
        raise NotImplementedError("Abstract class method called")
