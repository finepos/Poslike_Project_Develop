# app/routes/forecast.py
import json
from datetime import datetime, timedelta
import pandas as pd
from itertools import groupby
import re
from io import BytesIO
import numpy as np

from flask import render_template, current_app, request, jsonify, send_file
from sqlalchemy import func, or_
from openpyxl import Workbook

from . import bp
from ..models import Product, AnalyticsData
from ..extensions import db

# --- НАЛАШТУВАННЯ АЛГОРИТМУ ПРОГНОЗУВАННЯ ---
ORDER_CYCLE = 30
Z_SCORE = 1.65
MIN_SALES_FOR_TREND = 4
AGGRESSIVENESS_FACTOR = 1.5 # Множник для збільшення обсягу замовлення (1.0 = нейтрально, >1 = агресивно)

@bp.route('/forecast')
def forecast_index():
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
    try:
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        search_categories = request.args.getlist('search_category')

        if not search_categories:
            return jsonify({})

        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
        num_days_in_period = (end_date - start_date).days + 1
        if num_days_in_period <= 1: num_days_in_period = 1

        category_conditions = [Product.xml_data.like(f'%\"product_category\": \"{cat.replace("\"", "\"\"")}\"%') for cat in search_categories]
        query = Product.query.filter(or_(*category_conditions))

        param_filters, show_param_filters = {}, False
        if len(search_categories) == 1:
            param_query = Product.query.filter(
                Product.xml_data.like(f'%\"product_category\": \"{search_categories[0].replace("\"", "\"\"")}\"%')
            ).with_entities(Product.xml_data)
            
            products_xml_in_cat = param_query.all()
            params_for_this_cat = {key for xml_tuple in products_xml_in_cat if xml_tuple[0] for key in (json.loads(xml_tuple[0]).get('product_params') or {})}

            if params_for_this_cat:
                show_param_filters = True
                param_name_map = {re.sub(r'[\s/]+', '_', name): name for name in params_for_this_cat}
                for key, value in request.args.items():
                    if key.startswith('param_') and value:
                        original_name = param_name_map.get(key[len('param_'):])
                        if original_name:
                            param_filters[original_name] = value
                            query = query.filter(Product.xml_data.like(f'%"{original_name}": "{value}"%'))
        
        all_products = query.all()

        sales_history = AnalyticsData.query.with_entities(
            AnalyticsData.analytics_product_sku, AnalyticsData.analytics_product_quantity, AnalyticsData.analytics_sale_date
        ).all()

        if not sales_history:
            return jsonify({'products_by_category': {}, 'show_param_filters': show_param_filters, 'param_filters': param_filters})

        df = pd.DataFrame(sales_history, columns=['sku', 'quantity', 'sale_date'])
        df['sale_date'] = pd.to_datetime(df['sale_date'], errors='coerce')
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0)
        df.dropna(subset=['sale_date'], inplace=True)
        
        df_period = df[(df['sale_date'] >= start_date) & (df['sale_date'] <= end_date)]

        products_to_order = []
        for product in all_products:
            product_sales = df_period[df_period['sku'].str.startswith(product.sku, na=False)]
            
            if product_sales.empty:
                continue

            daily_sales = product_sales.groupby(product_sales['sale_date'].dt.date)['quantity'].sum().reindex(pd.date_range(start=start_date, end=end_date, freq='D').date, fill_value=0)
            
            non_zero_sales = daily_sales[daily_sales > 0]
            
            if len(non_zero_sales) > 3:
                Q1 = non_zero_sales.quantile(0.25)
                Q3 = non_zero_sales.quantile(0.75)
                IQR = Q3 - Q1
                upper_bound = Q3 + 1.5 * IQR
                normal_sales = non_zero_sales[non_zero_sales <= upper_bound]
            else:
                normal_sales = non_zero_sales

            if len(normal_sales) >= MIN_SALES_FOR_TREND:
                x_values = np.array([(d - daily_sales.index[0]).days for d in normal_sales.index])
                y_values = normal_sales.values
                
                slope, intercept = np.polyfit(x_values, y_values, 1)
                predicted_magnitude = slope * (num_days_in_period - 1) + intercept
                sales_frequency = len(normal_sales) / num_days_in_period
                projected_daily_demand = max(0, predicted_magnitude * sales_frequency)

                predicted_y = slope * x_values + intercept
                see = np.sqrt(np.sum((y_values - predicted_y)**2) / len(x_values)) if len(x_values) > 2 else daily_sales.std()
            else:
                projected_daily_demand = daily_sales.mean()
                see = daily_sales.std()

            safety_stock = Z_SCORE * see * np.sqrt(product.delivery_time)
            demand_during_lead_time = projected_daily_demand * product.delivery_time
            reorder_point = demand_during_lead_time + safety_stock
            reorder_point = max(reorder_point, product.minimum_stock or 0)

            effective_stock = product.stock + product.in_transit_quantity
            
            if effective_stock <= reorder_point:
                # --- ПОЧАТОК ЗМІНИ: Застосування коефіцієнту агресивності ---
                demand_for_cycle = projected_daily_demand * ORDER_CYCLE * AGGRESSIVENESS_FACTOR
                # --- КІНЕЦЬ ЗМІНИ ---
                
                target_inventory_level = demand_for_cycle + demand_during_lead_time + safety_stock
                order_quantity = round(target_inventory_level - effective_stock)

                if order_quantity > 0:
                    products_to_order.append({
                        'sku': product.sku, 'name': product.name, 'stock': product.stock,
                        'in_transit': product.in_transit_quantity, 'delivery_time': product.delivery_time,
                        'sales_in_period': int(daily_sales.sum()),
                        'request_count': len(product_sales),
                        'order_quantity': order_quantity, 'category': json.loads(product.xml_data or '{}').get('product_category', "Без категорії")
                    })

        products_by_category = {k: list(v) for k, v in groupby(sorted(products_to_order, key=lambda x: x['category']), key=lambda x: x['category'])}
        return jsonify({'products_by_category': products_by_category, 'show_param_filters': show_param_filters, 'param_filters': param_filters})

    except Exception as e:
        current_app.logger.error(f"Forecast API error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/forecast/export-xls', methods=['POST'])
def export_forecast_xls():
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
            ws.append([product_data.get('sku'), product_data.get('name'), product_data.get('order_quantity')])
        ws.column_dimensions['A'].width = 20
        ws.column_dimensions['B'].width = 80
        ws.column_dimensions['C'].width = 20
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        filename = f"forecast_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
        return send_file(buffer, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f"Forecast export error: {e}", exc_info=True)
        return "Помилка при створенні файлу.", 500