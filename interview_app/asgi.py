import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'interview_app.settings')
django.setup()

django_asgi_app = get_asgi_application()

# Now import FastAPI after Django setup
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from api.main import app as fastapi_app

# Mount Django ASGI app on FastAPI
fastapi_app.mount("/django", django_asgi_app)

application = fastapi_app