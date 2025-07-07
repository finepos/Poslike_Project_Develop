# run.py
import logging
from app import create_app
from app.initialization import init_app_data
from app.sync import sync_products_from_xml
from app.queue_worker import process_print_queue
from app.extensions import db, scheduler
from app.models import Setting

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = create_app()

def run_with_context(func):
    """Створює обгортку, яка виконує функцію всередині контексту додатку."""
    def wrapper():
        with app.app_context():
            func()
    return wrapper

with app.app_context():
    # ▼▼▼ ОНОВЛЕНА ЛОГІКА СТВОРЕННЯ ТАБЛИЦЬ ▼▼▼
    
    # 1. Створюємо таблиці для основної бази даних (stock_control.db)
    db.create_all()
    
    # 2. Явно створюємо таблиці для бази даних аналітики (analytics.db)
    analytics_engine = db.get_engine(app, bind='analytics')
    if analytics_engine:
        db.metadata.create_all(bind=analytics_engine)

    # ▲▲▲ КІНЕЦЬ ОНОВЛЕННЯ ▲▲▲

    init_app_data(app)

    logging.info("Запуск початкової синхронізації XML...")
    sync_products_from_xml()
    logging.info("Початкова синхронізація завершена.")

    sync_interval_setting = Setting.query.get('sync_interval_minutes')
    sync_interval = int(sync_interval_setting.value) if sync_interval_setting else 60

    sync_job_func = run_with_context(sync_products_from_xml)
    print_queue_func = run_with_context(process_print_queue)
    
    if scheduler.get_job('sync_job'):
        scheduler.modify_job('sync_job', trigger='interval', minutes=sync_interval)
        logging.info(f"Завдання 'sync_job' переплановано з інтервалом {sync_interval} хв.")
    else:
        scheduler.add_job(id='sync_job', func=sync_job_func, trigger='interval', minutes=sync_interval)
        logging.info(f"Завдання 'sync_job' додано з інтервалом {sync_interval} хв.")

    if not scheduler.get_job('print_queue_job'):
        scheduler.add_job(id='print_queue_job', func=print_queue_func, trigger='interval', seconds=5)
        logging.info("Завдання 'print_queue_job' додано.")

    if scheduler.state == 2:
        scheduler.resume()
        logging.info("Планувальник завдань відновлено.")
    elif scheduler.state == 0:
        scheduler.start()
        logging.info("Планувальник завдань запущено.")
    else:
        logging.info("Планувальник завдань вже працює.")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5050, debug=True, use_reloader=False)