"""
wsgi.py — Production entry point for gunicorn
gunicorn app:app  OR  gunicorn wsgi:application
"""
import os
os.makedirs(os.getenv("OUTPUT_DIR", "./output"), exist_ok=True)

from app import app as application

if __name__ == "__main__":
    application.run()
