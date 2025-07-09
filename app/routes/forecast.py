# app/routes/forecast.py
import json
from datetime import datetime, timedelta
import pandas as pd
from itertools import groupby
import re
from io import BytesIO
import numpy as np # Імпортуємо numpy для розрахунків

# ▼▼▼ ДОДАНО ІМПОРТИ ▼▼▼
from flask import render_template, current_app, request, jsonify, send_file
from sqlalchemy import func, or_
from openpyxl import Workbook

from . import bp
from ..models import Product, AnalyticsData
from ..extensions import db

# Налаштування алгоритму
SAFETY_BUFFER_DAYS = 0
MIN_SALES_FOR_FORECAST = 1
LEAD_TIME_MULTIPLIER = 2.0
# Відсоток від найбільшого замовлення, який потрібно тримати на складі для покриття беклогу
LARGE_ORDER_COVERAGE_PERCENT = 0.6

@bp.route('/forecast')
def forecast_index():
    """
    Ця функція тепер просто відображає порожню сторінку прогнозування з фільтрами.
    """
    try:
        all_categories = sorted({
            d.get('product_category')
            for p in Product.query.with_entities(Product.xml_data).all()
            if p.xml_data and (d := json.loads(p.xml_data)) and d.get('product_category')
        })

        today = datetime.now()
        start_of_year = today.replace(month=1, day=1)

        start_date_default = start_of_year.strftime('%Y-%m-%d')
        end_date_default = today.strftime('%Y-%m-%d')

        return render_template("forecast.html",
                               all_categories=all_categories,
                               start_date=request.args.get('start_date', start_date_default),
                               end_date=request.args.get('end_date', end_date_default))
    except Exception as e:
        current_app.logger.error(f"Forecast page error: {e}", exc_info=True)
        return render_template("forecast.html", error=str(e), all_categories=[])


@bp.route('/api/forecast/calculate')
def calculate_forecast_api():
    """
    API-ендпоінт, який виконує всі розрахунки та повертає результат у форматі JSON.
    """
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        search_categories = request.args.getlist('search_category')

        if not search_categories:
            return jsonify({})

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')

        num_days_in_period = (end_date - start_date).days + 1
        if num_days_in_period <= 0: num_days_in_period = 1

        category_conditions = [Product.xml_data.like(f'%\"product_category\": \"{cat.replace("\"", "\"\"")}\"%') for cat in search_categories]
        query = Product.query.filter(or_(*category_conditions))

        param_filters = {}
        show_param_filters = False

        if len(search_categories) == 1:
            products_in_cat = query.with_entities(Product.xml_data).all()
            params_for_this_cat = set()
            for p_xml, in products_in_cat:
                if p_xml:
                    try: params_for_this_cat.update(json.loads(p_xml).get('product_params', {}).keys())
                    except json.JSONDecodeError: continue

            if params_for_this_cat:
                show_param_filters = True
                param_name_map = {re.sub(r'[\s/]+', '_', name): name for name in params_for_this_cat}
                for key, value in request.args.items():
                    if key.startswith('param_') and value:
                        original_name = param_name_map.get(key[len('param_'):])
                        if original_name:
                            param_filters[original_name] = value
                            query = query.filter(Product.xml_data.like(f'%"{original_name.replace("\"", "\"\"")}": "{value.replace("\"", "\"\"")}"%'))

        all_products = query.all()

        sales_history = AnalyticsData.query.with_entities(
            AnalyticsData.analytics_product_sku, AnalyticsData.analytics_product_quantity, AnalyticsData.analytics_sale_date
        ).all()

        if not sales_history:
            return jsonify({'products_by_category': {}, 'show_param_filters': show_param_filters, 'param_filters': param_filters})

        df = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date'])
        df['sale_date'] = pd.to_datetime(df['sale_date'], errors='coerce')
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce')
        df.dropna(subset=['sale_date', 'quantity'], inplace=True)
        
        df = df[(df['sale_date'] >= start_date) & (df['sale_date'] <= end_date)]

        products_to_order = []
        for product in all_products:
            product_sales = df[df['sku'] == product.sku]
            
            daily_sales = product_sales.groupby(product_sales['sale_date'].dt.date)['quantity'].sum()
            all_days_in_period = pd.date_range(start=start_date, end=end_date, freq='D')
            daily_sales = daily_sales.reindex(all_days_in_period.date, fill_value=0)

            mean_sales = daily_sales.mean()
            std_dev = daily_sales.std()
            cv = std_dev / mean_sales if mean_sales > 0 else float('inf')
            stability_factor = 1 / (1 + cv) if cv != float('inf') else 0
            
            # --- РОЗРАХУНОК ТРЕНДУ ---
            trend_factor = 1.0
            if len(daily_sales) > 1:
                x = np.arange(len(daily_sales))
                y = daily_sales.values
                # Використовуємо лінійну регресію для знаходження нахилу тренду
                slope, _ = np.polyfit(x, y, 1)
                # Нормалізуємо нахил, щоб отримати денний фактор росту/падіння
                daily_growth_rate = slope / mean_sales if mean_sales > 0 else 0
                trend_factor = 1.0 + daily_growth_rate
                # Обмежуємо фактор, щоб уникнути екстремальних значень
                trend_factor = max(0.5, min(2.0, trend_factor))

            total_sold_in_period = product_sales['quantity'].sum()
            total_requests_in_period = len(product_sales)
            demand_metric = max(total_sold_in_period, total_requests_in_period)

            if demand_metric < MIN_SALES_FOR_FORECAST:
                continue

            avg_daily_demand = demand_metric / num_days_in_period
            if avg_daily_demand <= 0:
                continue
            
            max_order_size = product_sales['quantity'].max() if not product_sales.empty else 0
            backlog_demand = max_order_size * LARGE_ORDER_COVERAGE_PERCENT

            statistical_reorder_point = avg_daily_demand * product.delivery_time
            manual_minimum = product.minimum_stock or 0
            reorder_point = max(statistical_reorder_point, manual_minimum)

            effective_stock = product.stock + product.in_transit_quantity

            if effective_stock <= reorder_point:
                # "Майбутній запас" тепер коригується на стабільність та тренд
                future_supply = (avg_daily_demand * product.delivery_time * LEAD_TIME_MULTIPLIER * stability_factor * trend_factor)
                target_stock = reorder_point + future_supply + backlog_demand
                
                order_quantity = round(target_stock - effective_stock)
                if order_quantity <= 0:
                    continue

                category = json.loads(product.xml_data).get('product_category', "Без категорії") if product.xml_data else "Без категорії"
                products_to_order.append({
                    'sku': product.sku, 'name': product.name, 'stock': product.stock,
                    'in_transit': product.in_transit_quantity, 'delivery_time': product.delivery_time,
                    'sales_in_period': int(total_sold_in_period),
                    'order_quantity': order_quantity, 'category': category
                })

        products_to_order.sort(key=lambda x: (x['category'], x['sku']))
        products_by_category = {key: list(group) for key, group in groupby(products_to_order, key=lambda x: x['category'])}

        return jsonify({
            'products_by_category': products_by_category,
            'show_param_filters': show_param_filters,
            'param_filters': param_filters
        })

    except Exception as e:
        current_app.logger.error(f"Forecast API error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@bp.route('/forecast/export-xls', methods=['POST'])
def export_forecast_xls():
    """
    Генерує та відправляє XLS-файл з обраними для замовлення товарами.
    """
    selected_products_json = request.form.get('selected_products')
    if not selected_products_json:
        return "Не обрано товари для експорту", 400

    try:
        selected_products = json.loads(selected_products_json)
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Прогноз замовлення"
        
        ws.append(['SKU', 'Назва товару', 'К-ть до замовлення'])
        
        for product_data in selected_products:
            ws.append([
                product_data.get('sku'),
                product_data.get('name'),
                product_data.get('order_quantity')
            ])
            
        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 80
        ws.column_dimensions['C'].width = 20

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        
        filename = f"forecast_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        current_app.logger.error(f"Forecast export error: {e}", exc_info=True)
        return "Помилка при створенні файлу.", 500