from flask import request, jsonify, flash, redirect, url_for, current_app
from sqlalchemy import func
import json

from . import bp
from ..extensions import db
from ..models import Product, Printer, PrintJob, Currency
from ..printing import generate_zpl_code, check_printer_status


# ▼▼▼ ПОЧАТОК: ПОВНІСТЮ ЗАМІНІТЬ ІСНУЮЧУ ФУНКЦІЮ НА ЦЮ ▼▼▼
@bp.route('/execute-print', methods=['POST'])
def execute_print():
    try:
        printer_id = request.form.get('printer_id')
        printer = Printer.query.get(printer_id)
        if not printer:
            return jsonify({'status': 'error', 'message': 'Принтер не знайдено!'}), 404

        product_id = request.form.get('product_id')
        product = None

        if product_id:
            # Стандартний друк з головної сторінки
            product = Product.query.get(product_id)
            if not product:
                 return jsonify({'status': 'error', 'message': 'Товар не знайдено!'}), 404
        else:
            # Друк зі сторінки накладної (без ID товару в базі)
            # ВИПРАВЛЕНО: Створюємо словник з усіма потрібними даними для ZPL
            product_data_for_zpl = {
                'product_sku': request.form.get('sku'),
                'product_name': request.form.get('name'),
                'product_url': request.form.get('product_url', '')
            }
            # Створюємо "псевдо-продукт" на льоту
            product = type('obj', (object,), {
                'id': None,
                'sku': request.form.get('sku'),
                'name': request.form.get('name'),
                'price': float(request.form.get('price', 0)),
                'xml_data': json.dumps(product_data_for_zpl, ensure_ascii=False)
            })

        quantity = int(request.form.get('quantity', 1))
        sorting_quantity = request.form.get('sorting_quantity')
        display_currency_code = request.form.get('display_currency', 'UAH')
        
        currency = Currency.query.filter_by(code=display_currency_code).first()
        rate = currency.rate if currency else 1.0
        
        converted_price = (product.price / rate) if product_id and rate > 0 else product.price
        
        zpl_code = generate_zpl_code(printer, product, sorting_quantity, quantity, override_price=converted_price)

        new_job = PrintJob(printer_id=printer.id, zpl_code=zpl_code)
        db.session.add(new_job)
        db.session.commit()
        message = f"Додано завдання ({quantity} шт.) до черги для принтера '{printer.name}'."
        return jsonify({'status': 'success', 'message': message})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Print execution error: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': f'Помилка сервера: {e}'}), 500
# ▲▲▲ КІНЕЦЬ ЗАМІНИ ФУНКЦІЇ ▲▲▲


@bp.route('/settings/clear-print-queue/<int:printer_id>', methods=['POST'])
def clear_print_queue(printer_id):
    try:
        printer = Printer.query.get_or_404(printer_id)
        num_deleted = PrintJob.query.filter_by(printer_id=printer.id).delete()
        db.session.commit()
        flash(f"Чергу для принтера '{printer.name}' очищено. Видалено завдань: {num_deleted}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Помилка під час очищення черги: {e}", "danger")
    return redirect(url_for('main.settings'))


@bp.route('/settings/check-printer-status/<int:printer_id>')
def check_printer_status_route(printer_id):
    printer = Printer.query.get_or_404(printer_id)
    is_ready, message = check_printer_status(printer.ip_address, printer.port)
    return jsonify({'is_ready': is_ready, 'message': message})


@bp.route('/execute-bulk-print', methods=['POST'])
def execute_bulk_print():
    try:
        product_ids_str = request.form.get('product_ids')
        printer_id = request.form.get('printer_id')
        quantity = int(request.form.get('quantity', 1))
        sorting_quantity = request.form.get('sorting_quantity')
        display_currency_code = request.form.get('display_currency', 'UAH')

        if not product_ids_str:
            return jsonify({'status': 'error', 'message': 'Товари не обрано.'}), 400
            
        product_ids = [int(pid) for pid in product_ids_str.split(',')]
        products = Product.query.filter(Product.id.in_(product_ids)).all()
        printer = Printer.query.get(printer_id)
        
        if not products or not printer:
            return jsonify({'status': 'error', 'message': 'Товари або принтер не знайдено!'}), 404
            
        currency = Currency.query.filter_by(code=display_currency_code).first()
        rate = currency.rate if currency and currency.rate > 0 else 1.0
        
        full_zpl_code = ""
        for product in products:
            converted_price = product.price / rate
            zpl_for_one_set = generate_zpl_code(printer, product, sorting_quantity, quantity, override_price=converted_price)
            full_zpl_code += zpl_for_one_set
            
        if not full_zpl_code.strip():
            return jsonify({'status': 'error', 'message': 'Не вдалося згенерувати ZPL-код.'}), 500
            
        new_job = PrintJob(printer_id=printer.id, zpl_code=full_zpl_code)
        db.session.add(new_job)
        db.session.commit()
        message = f"Додано завдання ({len(products)} товарів по {quantity} шт.) до черги для '{printer.name}'."
        return jsonify({'status': 'success', 'message': message})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Bulk print error: {e}")
        return jsonify({'status': 'error', 'message': f'Помилка сервера: {e}'}), 500