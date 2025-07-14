import sqlite3
import os

# Шлях до вашої основної бази даних
db_path = os.path.join('instance', 'stock_control.db')

if not os.path.exists(db_path):
    print(f"Помилка: Файл бази даних не знайдено за шляхом: {db_path}")
else:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print("Підключено до stock_control.db.")

        # Додаємо нову колонку 'cost_price' до таблиці 'in_transit_order'
        print("Спроба додати колонку 'cost_price' до таблиці 'in_transit_order'...")
        cursor.execute("ALTER TABLE in_transit_order ADD COLUMN cost_price FLOAT")

        conn.commit()
        print("Успіх! Колонку 'cost_price' було успішно додано.")

    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Інформація: Колонка 'cost_price' вже існує в таблиці.")
        else:
            print(f"Сталася помилка SQLite: {e}")
    except Exception as e:
        print(f"Сталася непередбачена помилка: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()
            print("З'єднання з базою даних закрито.")