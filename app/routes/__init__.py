from flask import Blueprint

# Створюємо Blueprint і явно вказуємо, де знаходяться шаблони.
# 'template_folder' вказує на папку 'templates' всередині батьківської директорії ('../')
bp = Blueprint('main', __name__, template_folder='../templates')

# Додаємо імпорт нового маршруту 'salesdrive'
from . import api, main, printing, product, settings, salesdrive