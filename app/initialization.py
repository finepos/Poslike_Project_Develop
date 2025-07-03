# app/initialization.py
from .extensions import db
from .models import ColorSetting, Setting

# ▼▼▼ ДОДАНО sort_order ДО КОЖНОГО ЕЛЕМЕНТУ ▼▼▼
DEFAULT_COLORS = [
    {'level_name': 'status-level-5', 'background_color': '#c0f5b0', 'text_color': '#000000', 'label': '100% - 81%', 'sort_order': 10},
    {'level_name': 'status-level-4', 'background_color': '#ecf5b0', 'text_color': '#000000', 'label': '80% - 61%', 'sort_order': 20},
    {'level_name': 'status-level-3', 'background_color': '#f5e3b0', 'text_color': '#000000', 'label': '60% - 41%', 'sort_order': 30},
    {'level_name': 'status-level-2', 'background_color': '#f5cfb0', 'text_color': '#000000', 'label': '40% - 21%', 'sort_order': 40},
    {'level_name': 'status-level-1', 'background_color': '#f5b9b0', 'text_color': '#000000', 'label': '20% - >0%', 'sort_order': 50},
    {'level_name': 'status-level-0', 'background_color': '#e0e0e0', 'text_color': '#000000', 'label': 'Залишок = 0', 'sort_order': 60},
    {'level_name': 'status-critical', 'background_color': '#f5b0b0', 'text_color': '#ffffff', 'label': 'Нижче мінімуму', 'sort_order': 70}
]

def populate_default_colors():
    """Видаляє існуючі налаштування кольорів та створює стандартні."""
    ColorSetting.query.delete()
    for color_data in DEFAULT_COLORS:
        db.session.add(ColorSetting(**color_data))
    db.session.commit()
    print("Створено стандартні налаштування кольорів з порядком сортування.")

def init_app_data(app):
    """Створює таблиці та початкові налаштування, якщо їх немає."""
    with app.app_context():
        db.create_all()
        # ▼▼▼ ЗМІНЕНО УМОВУ ПЕРЕВІРКИ ▼▼▼
        # Перевіряємо, чи кількість налаштувань не відповідає кількості за замовчуванням.
        if ColorSetting.query.count() != len(DEFAULT_COLORS):
            populate_default_colors()

        if Setting.query.get('xml_url') is None:
            db.session.add(Setting(key='xml_url', value='https://printec.salesdrive.me/export/yml/export.yml?publicKey=i1wcuj4-4oGrmFhj9I6iyLpolm12OeeDhdqg_w8XH_Qitjr4zud3seyWgBpJ'))
            db.session.commit()
        if Setting.query.get('sync_interval_minutes') is None:
            db.session.add(Setting(key='sync_interval_minutes', value='60'))
            db.session.commit()