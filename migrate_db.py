import sqlite3
import os

# Шлях до вашої бази даних
db_path = os.path.join('instance', 'analytics.db')

# Перевіряємо, чи існує файл бази даних
if not os.path.exists(db_path):
    print(f"Помилка: Файл бази даних не знайдено за шляхом: {db_path}")
else:
    try:
        # Підключаємося до бази даних
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        print("Підключено до бази даних analytics.db.")

        # Додаємо нову колонку 'category' до таблиці 'trained_forecast_model'
        # UNIQUE та NOT NULL додано для відповідності моделі SQLAlchemy
        cursor.execute("ALTER TABLE trained_forecast_model ADD COLUMN category VARCHAR(255) NOT NULL DEFAULT 'Без категорії'")

        # Зберігаємо зміни
        conn.commit()
        print("Успіх! Колонку 'category' було додано до таблиці 'trained_forecast_model'.")

    except sqlite3.OperationalError as e:
        # Ця помилка виникне, якщо колонка вже існує. Це нормально.
        if "duplicate column name" in str(e):
            print("Інформація: Колонка 'category' вже існує в таблиці.")
        else:
            print(f"Сталася помилка SQLite: {e}")
    except Exception as e:
        print(f"Сталася непередбачена помилка: {e}")
    finally:
        # Закриваємо з'єднання
        if 'conn' in locals() and conn:
            conn.close()
            print("З'єднання з базою даних закрито.")