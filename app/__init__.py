# app/__init__.py
from flask import Flask
from .config import Config
from .extensions import db, scheduler
from .models import Currency, Setting 
from .utils import format_time_ago
from datetime import datetime

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    
    app.config.from_object(Config)
    app.jinja_env.add_extension('jinja2.ext.do')
    
    db.init_app(app)
    # Ініціалізуємо планувальник, але не запускаємо його тут
    if not scheduler.running:
        scheduler.init_app(app)
        scheduler.start(paused=True) # Запускаємо в режимі паузи

    @app.context_processor
    def inject_currencies():
        try:
            currencies = Currency.query.order_by(Currency.id).all()
        except Exception:
            currencies = []
        return dict(available_currencies=currencies)

    @app.context_processor
    def inject_last_sync_time():
        """
        Робить час і статус останньої синхронізації доступним у всіх шаблонах.
        Перевіряє наявність помилок.
        """
        sync_status = {'text': 'невідомо', 'is_error': True}
        try:
            last_sync_setting = Setting.query.get('last_sync_time')
            last_error_setting = Setting.query.get('last_sync_error')

            last_sync_time = datetime.fromisoformat(last_sync_setting.value) if last_sync_setting else None
            
            if last_error_setting:
                error_time_str, error_msg = last_error_setting.value.split('|', 1)
                last_error_time = datetime.fromisoformat(error_time_str)
                
                # Якщо помилка новіша за останню успішну синхронізацію, показуємо помилку
                if not last_sync_time or last_error_time > last_sync_time:
                    error_time_ago = format_time_ago(error_time_str)
                    sync_status['text'] = f"Помилка синхронізації ({error_time_ago})"
                    sync_status['is_error'] = True
                    return dict(sync_status=sync_status)

            if last_sync_time:
                sync_status['text'] = f"Синхронізовано: {format_time_ago(last_sync_setting.value)}"
                sync_status['is_error'] = False
            else:
                sync_status['text'] = "Синхронізація ще не проводилась"
                sync_status['is_error'] = True

        except Exception:
             sync_status = {'text': 'невідомо', 'is_error': True}

        return dict(sync_status=sync_status)

    # Реєструємо Blueprint
    with app.app_context():
        from .routes import bp
        app.register_blueprint(bp)

        # Переконуємось, що моделі імпортовані
        from . import models
        
        # --- ▼▼▼ ДОДАЙТЕ ЦЕЙ КОД ▼▼▼ ---
        # Створюємо всі таблиці, включаючи ті, що в 'analytics' bind
        db.create_all()

    return app