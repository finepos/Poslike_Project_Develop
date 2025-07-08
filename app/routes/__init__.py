from flask import Blueprint

bp = Blueprint('main', __name__, template_folder='../templates')

# Додаємо імпорт нового маршруту 'forecast'
from . import api, main, printing, product, settings, salesdrive, analytics, forecast