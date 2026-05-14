import os
from django.core.wsgi import get_wsgi_application

# Asegúrate de que el path coincida con el nombre de tu carpeta de proyecto
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'asr1_disponibilidad.app.settings')

application = get_wsgi_application()
