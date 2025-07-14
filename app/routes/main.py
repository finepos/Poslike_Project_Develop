# app/routes/main.py
import json
import re
from math import ceil
from flask import render_template, request, flash, redirect, url_for # <-- Додано flash та redirect
from sqlalchemy import or_, func
from sqlalchemy.orm import joinedload
from datetime import datetime # <-- ДОДАНО ЦЕЙ ІМПОРТ

from . import bp
from ..extensions import db # <-- ДОДАНО ЦЕЙ ІМПОРТ
from ..models import Product, Printer, ColorSetting, InTransitInvoice, InTransitOrder, Currency # <-- Додайте Currency
from ..utils import get_pagination_window, natural_sort_key


@bp.route('/')
def index():
    # ... (код цієї функції залишається без змін) ...
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
            param_name_map = {re.sub(r'[\s/]+', '_', name): name for name in params_for_this_cat}
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

    final_products_to_display = []
    for p in all_filtered_products:
        xml_data = json.loads(p.xml_data) if p.xml_data else {}
        category = xml_data.get('product_category')
        bm = benchmark_stock.get(category, 0)
        
        status = ''
        if p.minimum_stock is not None and p.stock < p.minimum_stock:
            status = 'status-critical'
        elif p.stock == 0:
            status = 'status-level-0'
        else:
            perc = (p.stock / bm) * 100 if bm > 0 else 0
            if perc <= 20:
                status = 'status-level-1'
            elif perc <= 40:
                status = 'status-level-2'
            elif perc <= 60:
                status = 'status-level-3'
            elif perc <= 80:
                status = 'status-level-4'
            else:
                status = 'status-level-5'
        
        item_data = {'product': p, 'product_url': xml_data.get('product_url', ''), 'product_picture': xml_data.get('product_picture', ''), 'status_class': status}
        
        if not stock_level_filter or stock_level_filter == status:
            final_products_to_display.append(item_data)

    if sort_by == 'stock':
        reverse = sort_order == 'desc'
        final_products_to_display.sort(key=lambda item: item['product'].stock, reverse=reverse)
    elif sort_by == 'price':
        reverse = sort_order == 'desc'
        final_products_to_display.sort(key=lambda item: item['product'].price, reverse=reverse)
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
    
    return render_template('index.html', products=paginated_products, pagination=pagination, pagination_args={k:v for k,v in request.args.items() if k != 'page'},
                           search_sku=search_sku, search_name=search_name, search_categories=search_categories,
                           stock_filter=stock_filter, stock_quantity=stock_quantity,
                           printers_json=json.dumps([p.to_dict() for p in Printer.query.all()]), categories=all_categories,
                           param_filters=param_filters, color_settings=[s.to_dict() for s in color_settings], stock_level_filter=stock_level_filter,
                           show_param_filters=show_param_filters,
                           sort_by=sort_by, sort_order=sort_order,
                           is_exact_sku_search=is_exact_sku_search,
                           DEFAULT_PER_PAGE=DEFAULT_PER_PAGE,
                           endpoint=request.endpoint)


@bp.route('/in-transit')
def in_transit_view():
    page = request.args.get('page', 1, type=int)
    
    query = InTransitInvoice.query.order_by(InTransitInvoice.invoice_date.desc(), InTransitInvoice.id.desc())
    
    search_term = request.args.get('search', '')
    if search_term:
        query = query.filter(
            or_(
                InTransitInvoice.invoice_number.ilike(f'%{search_term}%'),
                InTransitInvoice.comment.ilike(f'%{search_term}%')
            )
        )

    pagination = query.paginate(page=page, per_page=50, error_out=False)
    invoices = pagination.items

    return render_template('in_transit.html', 
                           invoices=invoices, 
                           pagination=pagination,
                           search_term=search_term)


@bp.route('/in-transit/<int:invoice_id>', methods=['GET', 'POST'])
def in_transit_detail_view(invoice_id):
    invoice = InTransitInvoice.query.get_or_404(invoice_id)
    
    if request.method == 'POST':
        try:
            data = request.get_json()
            
            # Оновлюємо заголовок накладної
            invoice.invoice_number = data.get('invoice_number')
            invoice.comment = data.get('comment')
            date_str = data.get('invoice_date')
            if date_str:
                invoice.invoice_date = datetime.strptime(date_str, '%Y-%m-%d').date()

            # Оновлюємо та додаємо товари
            items_data = data.get('items', [])
            existing_item_ids = {item.id for item in invoice.items}
            
            for item_data in items_data:
                product_id = int(item_data['product_id'])
                new_quantity = int(item_data['quantity'])
                item_id = item_data.get('item_id') # Може бути 'new' для нових товарів

                product = Product.query.get(product_id)
                if not product: continue

                if item_id != 'new':
                    # Оновлюємо існуючий товар
                    item_id = int(item_id)
                    order_item = InTransitOrder.query.get(item_id)
                    if order_item:
                        # Коригуємо загальну кількість "в дорозі"
                        quantity_diff = new_quantity - order_item.quantity
                        product.in_transit_quantity += quantity_diff
                        order_item.quantity = new_quantity
                else:
                    # Додаємо новий товар до накладної
                    new_order_item = InTransitOrder(
                        invoice_id=invoice.id,
                        product_id=product_id,
                        quantity=new_quantity
                    )
                    product.in_transit_quantity += new_quantity
                    db.session.add(new_order_item)

            db.session.commit()
            flash('Накладну успішно оновлено.', 'success')
            return jsonify({'status': 'success', 'redirect_url': url_for('main.in_transit_detail_view', invoice_id=invoice.id)})

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Invoice update error: {e}", exc_info=True)
            return jsonify({'status': 'error', 'message': f'Помилка оновлення: {e}'}), 500

    return render_template('in_transit_detail.html', invoice=invoice)

@bp.route('/in-transit/new')
def in_transit_new():
    # --- ПОЧАТОК ЗМІН: Завантажуємо валюти ---
    available_currencies = Currency.query.all()
    # --- КІНЕЦЬ ЗМІН ---
    return render_template('in_transit_new.html', available_currencies=available_currencies)