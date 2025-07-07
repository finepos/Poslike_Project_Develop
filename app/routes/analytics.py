# app/routes/analytics.py
import json
import re
from math import ceil
import os
import pandas as pd
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import render_template, request, flash, redirect, url_for, current_app, jsonify

from sqlalchemy import or_, func

from . import bp
from ..models import Product, Printer, ColorSetting, AnalyticsData, AnalyticsImport
from ..extensions import db
from ..utils import get_pagination_window, calculate_forecast, natural_sort_key

# Мапінг очікуваних назв стовпців у файлі на поля в моделі AnalyticsData
COLUMN_MAPPING = {
    'Дата створення': 'analytics_creation_date', 'Прізвище [Контакт]': 'analytics_last_name',
    'Ім\'я [Контакт]': 'analytics_first_name', 'По батькові [Контакт]': 'analytics_middle_name',
    'Телефон [Контакт]': 'analytics_phone', 'Сума': 'analytics_total_sum',
    'Спосіб оплати': 'analytics_payment_method', 'Спосіб доставки': 'analytics_delivery_method',
    'Адреса доставки [Служба доставки]': 'analytics_shipping_address', 'ТТН [Служба доставки]': 'analytics_tracking_number',
    'Статус [Служба доставки]': 'analytics_delivery_status', 'Статус': 'analytics_status',
    'Дата продажу': 'analytics_sale_date', 'Менеджер': 'analytics_manager', 'Сайт': 'analytics_website',
    'Коментар': 'analytics_comment', 'Оплачено': 'analytics_paid', 'Комісія': 'analytics_commission',
    'Контрагент [Контакт]': 'analytics_counterparty', 'Рекламна кампанія': 'analytics_ad_campaign',
    'Назва [Товари/Послуги]': 'analytics_product_name', 'Опис [Товари/Послуги]': 'analytics_product_description',
    'ID [Товари/Послуги]': 'analytics_product_id', 'SKU [Товари/Послуги]': 'analytics_product_sku',
    'Ціна за од. [Товари/Послуги]': 'analytics_product_price_per_unit', 'К-ть [Товари/Послуги]': 'analytics_product_quantity',
    'Знижка [Товари/Послуги]': 'analytics_product_discount', 'Сума [Товари/Послуги]': 'analytics_product_sum',
    'Залишок на складі [Товари/Послуги]': 'analytics_product_stock_balance', 'Виробник [Товари/Послуги]': 'analytics_product_manufacturer',
    'Постачальник [Товари/Послуги]': 'analytics_product_supplier', 'Нотатка [Товари/Послуги]': 'analytics_product_note',
    'Склад [Товари/Послуги]': 'analytics_product_warehouse', 'Коломия [Товари/Послуги]': 'analytics_product_kolomyia',
    'Чернівці [Товари/Послуги]': 'analytics_product_chernivtsi', 'Собівартість [Товари/Послуги]': 'analytics_product_cost_price',
    'Штрихкод [Товари/Послуги]': 'analytics_product_barcode', 'Комісія [Товари/Послуги]': 'analytics_product_commission',
    'Допродажі [Товари/Послуги]': 'analytics_product_upsell', 'Ярлики [Товари/Послуги]': 'analytics_product_tags'
}

@bp.route('/analytics')
def analytics_index():
    """
    Головна сторінка аналітики, що дублює функціонал головної сторінки
    з додаванням аналітичних даних.
    """
    page = request.args.get('page', 1, type=int)
    show_all = request.args.get('show_all')
    search_sku = request.args.get('search_sku', '')
    search_name = request.args.get('search_name', '')
    search_categories = request.args.getlist('search_category')
    stock_filter = request.args.get('stock_filter', '')
    stock_quantity = request.args.get('stock_quantity', '')
    stock_level_filter = request.args.get('stock_level', '')
    sort_by = request.args.get('sort_by')
    sort_order = request.args.get('sort_order', 'desc')

    query = Product.query

    if search_sku:
        query = query.filter(Product.sku == search_sku)
    
    if search_categories:
        category_conditions = [Product.xml_data.like(f'%\"product_category\": \"{cat.replace("\"", "\"\"")}\"%') for cat in search_categories]
        query = query.filter(or_(*category_conditions))
    
    param_filters = {}
    show_param_filters = False 
    if search_categories:
        category_param_sets = []
        for category in search_categories:
            products_in_cat = Product.query.filter(Product.xml_data.like(f'%\"product_category\": \"{category.replace("\"", "\"\"")}\"%')).with_entities(Product.xml_data).all()
            params_for_this_cat = set()
            for p_xml, in products_in_cat:
                if p_xml:
                    try:
                        params_for_this_cat.update(json.loads(p_xml).get('product_params', {}).keys())
                    except json.JSONDecodeError:
                        continue
            category_param_sets.append(params_for_this_cat)
        
        if category_param_sets and all(s == category_param_sets[0] for s in category_param_sets):
            show_param_filters = True
            param_name_map = {re.sub(r'[\s/]+', '_', name): name for name in category_param_sets[0]}
            for key, value in request.args.items():
                if key.startswith('param_') and value:
                    original_name = param_name_map.get(key[len('param_'):])
                    if original_name:
                        param_filters[original_name] = value
                        query = query.filter(Product.xml_data.like(f'%"{original_name.replace("\"", "\"\"")}": "{value.replace("\"", "\"\"")}"%'))

    if stock_filter and stock_quantity.isdigit():
        q = int(stock_quantity)
        if stock_filter == 'less': query = query.filter(Product.stock < q)
        elif stock_filter == 'more': query = query.filter(Product.stock > q)
        elif stock_filter == 'equal': query = query.filter(Product.stock == q)

    all_filtered_products = query.all()

    if search_name:
        search_words = search_name.lower().split()
        products_filtered_by_name = []
        for p in all_filtered_products:
            product_name_lower = p.name.lower()
            if all(word in product_name_lower for word in search_words):
                products_filtered_by_name.append(p)
        all_filtered_products = products_filtered_by_name

    products_by_category = {}
    for p in Product.query.all():
        if p.xml_data:
            try:
                category = json.loads(p.xml_data).get('product_category')
                if category:
                    products_by_category.setdefault(category, []).append(p)
            except (json.JSONDecodeError, AttributeError):
                continue
    
    benchmark_stock = {}
    for cat, stocks_list in products_by_category.items():
        if stocks_list:
            sorted_stocks = sorted([p.stock for p in stocks_list], reverse=True)
            top_20_percent_count = ceil(len(stocks_list) * 0.2) or 1
            top_stocks = sorted_stocks[:top_20_percent_count]
            benchmark_stock[cat] = sum(top_stocks) / len(top_stocks) if top_stocks else 0

    sales_query = db.session.query(
        AnalyticsData.analytics_product_sku,
        func.sum(func.cast(AnalyticsData.analytics_product_quantity, db.Integer)).label('total_sold'),
        func.count(AnalyticsData.id).label('request_count')
    ).group_by(AnalyticsData.analytics_product_sku).subquery()

    analytics_data = db.session.query(
        sales_query.c.analytics_product_sku,
        sales_query.c.total_sold,
        sales_query.c.request_count
    ).all()
    
    analytics_map = {sku: {'total_sold': sold, 'request_count': count} for sku, sold, count in analytics_data}

    final_products_to_display = []
    for p in all_filtered_products:
        xml_data = json.loads(p.xml_data) if p.xml_data else {}
        category = xml_data.get('product_category')
        bm = benchmark_stock.get(category, 0)
        
        status = ''
        if p.minimum_stock is not None and p.stock < p.minimum_stock: status = 'status-critical'
        elif p.stock == 0: status = 'status-level-0'
        else:
            perc = (p.stock / bm) * 100 if bm > 0 else 0
            if perc <= 20: status = 'status-level-1'
            elif perc <= 40: status = 'status-level-2'
            elif perc <= 60: status = 'status-level-3'
            elif perc <= 80: status = 'status-level-4'
            else: status = 'status-level-5'
        
        analytics_info = analytics_map.get(p.sku, {'total_sold': 0, 'request_count': 0})
        
        item_data = {
            'product': p, 'product_url': xml_data.get('product_url', ''),
            'product_picture': xml_data.get('product_picture', ''), 'status_class': status,
            'total_sold': analytics_info['total_sold'], 'request_count': analytics_info['request_count']
        }
        
        if not stock_level_filter or stock_level_filter == status:
            final_products_to_display.append(item_data)

    for item in final_products_to_display:
        avg_sales, days_left = calculate_forecast(item['product'].id, item['product'].stock)
        item['avg_sales_per_day'] = avg_sales
        item['days_left'] = days_left

    if sort_by == 'stock':
        final_products_to_display.sort(key=lambda item: item['product'].stock, reverse=(sort_order == 'desc'))
    elif sort_by == 'price':
        final_products_to_display.sort(key=lambda item: item['product'].price, reverse=(sort_order == 'desc'))
    elif show_param_filters:
        first_category = search_categories[0]
        params_in_cat = {k for p in products_by_category.get(first_category, []) if p.xml_data for k in (json.loads(p.xml_data).get('product_params', {}) or {})}
        if params_in_cat:
            dynamic_sort_order = sorted(list(params_in_cat), reverse=True)
            final_products_to_display.sort(key=lambda item: tuple(natural_sort_key(json.loads(item['product'].xml_data).get('product_params', {}).get(p)) for p in dynamic_sort_order) if item['product'].xml_data else ())

    DEFAULT_PER_PAGE = 100
    total = len(final_products_to_display)
    items_per_page = total if show_all and search_categories else DEFAULT_PER_PAGE
    start = (page - 1) * items_per_page
    paginated_products = final_products_to_display[start : start + items_per_page]

    pagination = { 'page': page, 'per_page': items_per_page, 'total': total, 'pages': ceil(total / items_per_page) }
    pagination['window'] = get_pagination_window(pagination['page'], pagination['pages'])

    all_categories = sorted({d.get('product_category') for p in Product.query.with_entities(Product.xml_data).all() if p.xml_data and (d := json.loads(p.xml_data)) and d.get('product_category')})
    color_settings = ColorSetting.query.order_by(ColorSetting.sort_order).all()
    is_exact_sku_search = True if search_sku and total == 1 else False
    
    return render_template('analytics/index.html', 
                           products=paginated_products, pagination=pagination, 
                           pagination_args={k:v for k,v in request.args.items() if k != 'page'},
                           search_sku=search_sku, search_name=search_name, search_categories=search_categories,
                           stock_filter=stock_filter, stock_quantity=stock_quantity,
                           printers_json=json.dumps([p.to_dict() for p in Printer.query.all()]), 
                           categories=all_categories, param_filters=param_filters, 
                           color_settings=[s.to_dict() for s in color_settings], 
                           stock_level_filter=stock_level_filter, show_param_filters=show_param_filters,
                           sort_by=sort_by, sort_order=sort_order,
                           is_exact_sku_search=is_exact_sku_search,
                           DEFAULT_PER_PAGE=DEFAULT_PER_PAGE)


@bp.route('/analytics/settings', methods=['GET', 'POST'])
def analytics_settings():
    """Сторінка налаштувань та імпорту."""
    if request.method == 'POST':
        if 'import_file' not in request.files:
            flash('Файл для імпорту не вибрано.', 'danger')
            return redirect(request.url)

        file = request.files['import_file']
        if file.filename == '':
            flash('Файл для імпорту не вибрано.', 'danger')
            return redirect(request.url)

        if file and file.filename.endswith(('.xls', '.xlsx')):
            try:
                df = pd.read_excel(file)
                
                matched_columns = set(df.columns) & set(COLUMN_MAPPING.keys())
                if len(matched_columns) < 10:
                    flash('Невірний формат імпорт файлу! Не знайдено мінімум 10 необхідних стовпців.', 'danger')
                    return redirect(request.url)

                min_date, max_date = None, None
                if 'Дата продажу' in df.columns:
                    sale_dates = pd.to_datetime(df['Дата продажу'], errors='coerce')
                    valid_dates = sale_dates.dropna()
                    if not valid_dates.empty:
                        min_date = valid_dates.min().date()
                        max_date = valid_dates.max().date()

                upload_folder = os.path.join(current_app.instance_path, 'analytics_uploads')
                os.makedirs(upload_folder, exist_ok=True)
                filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
                file_path = os.path.join(upload_folder, filename)
                file.seek(0) 
                file.save(file_path)

                new_import = AnalyticsImport(
                    original_filename=file.filename,
                    file_path=file_path,
                    data_period_start=min_date,
                    data_period_end=max_date
                )
                db.session.add(new_import)
                db.session.flush()

                for index, row in df.iterrows():
                    data_row = AnalyticsData(import_id=new_import.id, raw_data=row.to_json())
                    for col_name, model_field in COLUMN_MAPPING.items():
                        if col_name in row and pd.notna(row[col_name]):
                            # ▼▼▼ ОНОВЛЕНА ЛОГІКА ФОРМАТУВАННЯ ТЕЛЕФОНІВ ▼▼▼
                            value = str(row[col_name])
                            if col_name == 'Телефон [Контакт]':
                                cleaned_phone = re.sub(r'\D', '', value)
                                if len(cleaned_phone) == 10:
                                    value = '38' + cleaned_phone
                                else:
                                    value = cleaned_phone
                            setattr(data_row, model_field, value)
                            # ▲▲▲ КІНЕЦЬ ОНОВЛЕННЯ ▲▲▲
                    db.session.add(data_row)
                
                db.session.commit()
                flash('Файл успішно імпортовано!', 'success')

            except Exception as e:
                db.session.rollback()
                flash(f'Помилка під час обробки файлу: {e}', 'danger')

            return redirect(url_for('main.analytics_settings'))

    imports = AnalyticsImport.query.order_by(AnalyticsImport.import_date.desc()).all()
    return render_template('analytics/settings.html', imports=imports)


@bp.route('/analytics/delete/<int:import_id>', methods=['POST'])
def analytics_delete(import_id):
    """Видалення імпортованого файлу та даних."""
    import_to_delete = AnalyticsImport.query.get_or_404(import_id)
    try:
        if os.path.exists(import_to_delete.file_path):
            os.remove(import_to_delete.file_path)
        
        db.session.delete(import_to_delete)
        db.session.commit()
        flash(f'Імпорт "{import_to_delete.original_filename}" та всі пов\'язані дані видалено.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Помилка під час видалення: {e}', 'danger')
        
    return redirect(url_for('main.analytics_settings'))

# ▼▼▼ НОВИЙ МАРШРУТ ДЛЯ ОТРИМАННЯ ЗАЯВОК ▼▼▼
@bp.route('/api/analytics/applications/<string:sku>')
def get_applications_for_sku(sku):
    """API для отримання заявок за конкретним SKU."""
    try:
        product_info = AnalyticsData.query.filter_by(analytics_product_sku=sku).first()
        product_name = product_info.analytics_product_name if product_info else "Назву не знайдено"

        applications = AnalyticsData.query.filter_by(analytics_product_sku=sku).order_by(AnalyticsData.analytics_sale_date.desc()).all()
        
        apps_data = []
        for app in applications:
            apps_data.append({
                'sale_date': app.analytics_sale_date,
                'contact_name': f"{app.analytics_last_name or ''} {app.analytics_first_name or ''}".strip(),
                'phone': app.analytics_phone,
                'price_per_unit': app.analytics_product_price_per_unit,
                'quantity': app.analytics_product_quantity,
                'sum': app.analytics_product_sum
            })

        return jsonify({
            'sku': sku,
            'product_name': product_name,
            'applications': apps_data
            })
    except Exception as e:
        current_app.logger.error(f"Error fetching applications for SKU {sku}: {e}")
        return jsonify({'error': str(e)}), 500