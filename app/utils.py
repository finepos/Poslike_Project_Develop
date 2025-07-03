# app/utils.py
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from math import ceil

from .extensions import db
from .models import Sale
# --- ПОЧАТОК ЗМІН ---
# Імпортуємо всі стандартні шаблони
from .config import DEFAULT_ZPL_SORTING, DEFAULT_ZPL_NO_SORTING, DEFAULT_ZPL_100X100
# --- КІНЕЦЬ ЗМІН ---

def natural_sort_key(s):
    """Ключ для "природного" сортування рядків, що містять числа."""
    if s is None: return (1,)
    parts = tuple(int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', str(s)))
    return (0,) + parts

def get_pagination_window(current_page, total_pages, neighbors=2):
    """Створює вікно пагінації для великих списків сторінок."""
    if total_pages <= 2 * neighbors + 5: return list(range(1, total_pages + 1))
    pages = [1]
    if current_page > neighbors + 2: pages.append(None)
    start, end = max(2, current_page - neighbors), min(total_pages - 1, current_page + neighbors)
    for i in range(start, end + 1): pages.append(i)
    if current_page < total_pages - neighbors - 1: pages.append(None)
    if total_pages not in pages: pages.append(total_pages)
    return pages

def calculate_forecast(product_id, current_stock):
    """Розраховує середні продажі та прогнозовану кількість днів до закінчення товару."""
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    total_sales = db.session.query(func.sum(Sale.quantity_sold)).filter(Sale.product_id == product_id, Sale.sale_timestamp >= thirty_days_ago).scalar() or 0
    avg_sales = total_sales / 30.0
    days_left = current_stock / avg_sales if avg_sales > 0 else float('inf')
    return round(avg_sales, 2), int(days_left) if days_left != float('inf') else '∞'

# <<< ПОЧАТОК НОВИХ ЗМІН >>>
def get_default_template_for_size(is_for_sorting=False, label_size=None):
    """
    Повертає стандартний ZPL-шаблон в залежності від типу та РОЗМІРУ принтера.
    """
    # Пріоритет для розміру 100x100
    if label_size == '100x100':
        return DEFAULT_ZPL_100X100
    
    # Стандартна логіка для інших розмірів
    return DEFAULT_ZPL_SORTING if is_for_sorting else DEFAULT_ZPL_NO_SORTING
# <<< КІНЕЦЬ НОВИХ ЗМІН >>>


def get_all_placeholders():
    """Повертає словник усіх доступних змінних для ZPL-шаблонів."""
    return {
        'product_id': 'ID товару', 'product_sku': 'Артикул', 'product_name': 'Назва', 'product_description': 'Опис',
        'product_price': 'Ціна', 'product_price_currency': 'Валюта', 'product_quantity_in_stock': 'Залишок',
        'product_url': 'Посилання', 'product_category': 'Категорія', 'product_vendor': 'Виробник',
        'product_picture': 'Фото', 'product_param:Назва': 'Параметр', 'product_sorting_quantity': 'К-сть для сортування',
        'product_date': 'Поточна дата та час (напр. 12:34 28.06.2025)'
    }

def format_time_ago(dt_iso_str):
    """Форматує ISO-рядок дати у зручний для читання вигляд "тому назад"."""
    if not dt_iso_str:
        return "ніколи"
    
    try:
        dt = datetime.fromisoformat(dt_iso_str)
        if dt.tzinfo is None:
            # Якщо немає інформації про часову зону, припускаємо, що це UTC
            dt = dt.replace(tzinfo=timezone.utc)
            
        now = datetime.now(timezone.utc)
        diff = now - dt
        
        minutes = int(diff.total_seconds() / 60)
        
        if minutes < 1:
            return "щойно"
        if minutes < 60:
            return f"{minutes} хв. назад"
        
        hours = minutes // 60
        remaining_minutes = minutes % 60
        
        if remaining_minutes == 0:
            return f"{hours} год. назад"
        
        return f"{hours} год. {remaining_minutes} хв. назад"
    except (ValueError, TypeError):
        return "невідомо"