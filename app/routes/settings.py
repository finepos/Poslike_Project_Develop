import requests
import json
from flask import render_template, current_app, flash, redirect, url_for, jsonify, request
from sqlalchemy import func

from . import bp
from ..extensions import db, scheduler
from ..models import Printer, ColorSetting, PrintJob, Setting
from ..utils import get_all_placeholders, get_default_template_for_size
from ..initialization import populate_default_colors
from ..sync import sync_products_from_xml

def _update_setting(key, value):
    """Допоміжна функція для безпечного оновлення або створення налаштування."""
    if value is None and key not in ['salesdrive_password', 'salesdrive_cookies_json']:
        return 
    
    setting = Setting.query.get(key)
    if setting:
        setting.value = value if value is not None else ""
    else:
        if value is not None:
            db.session.add(Setting(key=key, value=value))


@bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if request.method == 'POST':
        form_type = request.form.get('form_type')
        
        if form_type == 'import_settings':
            xml_url = request.form.get('xml_url')
            interval_str = request.form.get('sync_interval_minutes', '60')
            
            _update_setting('xml_url', xml_url)
            _update_setting('sync_interval_minutes', interval_str)
            
            try:
                interval = int(interval_str)
                if not (30 <= interval <= 1440):
                    flash('Інтервал оновлення має бути між 30 та 1440 хвилинами.', 'danger')
                else:
                    # Використовуємо правильний метод 'modify_job'
                    if scheduler.get_job('sync_job'):
                        scheduler.modify_job('sync_job', trigger='interval', minutes=interval)
                    else:
                        scheduler.add_job(id='sync_job', func=sync_products_from_xml, trigger='interval', minutes=interval)
                    flash(f'Налаштування імпорту оновлено. Новий інтервал: {interval} хв.', 'success')
            except (ValueError, TypeError):
                flash('Некоректне значення для інтервалу оновлення.', 'danger')
            except Exception as e:
                flash(f'Помилка оновлення планувальника: {e}', 'danger')

        elif form_type == 'salesdrive_settings':
            _update_setting('salesdrive_domain', request.form.get('salesdrive_domain'))
            _update_setting('salesdrive_login', request.form.get('salesdrive_login'))
            _update_setting('salesdrive_password', request.form.get('salesdrive_password'))
            _update_setting('salesdrive_cookies_json', '')
            flash("Налаштування SalesDrive збережено. При наступному запиті буде виконано новий вхід.", "success")
            
        elif form_type == 'add_printer':
             if 'is_default' in request.form:
                 Printer.query.update({Printer.is_default: False})
             new_printer = Printer(
                 name=request.form.get('name'),
                 ip_address=request.form.get('ip_address'),
                 port=int(request.form.get('port')),
                 label_size=request.form.get('label_size'),
                 is_default='is_default' in request.form,
                 is_for_sorting='is_for_sorting' in request.form
             )
             db.session.add(new_printer)
             flash("Принтер успішно додано.", "success")
             
        elif form_type == 'color_settings':
            for key, value in request.form.items():
                if key.startswith('bg_'):
                    level_name = key[3:]
                    setting = ColorSetting.query.filter_by(level_name=level_name).first()
                    if setting:
                        setting.background_color = value
                        setting.text_color = request.form.get(f'text_{level_name}')
            flash("Налаштування кольорів оновлено.", "success")
        
        db.session.commit()
        return redirect(url_for('main.settings'))

    job_counts = dict(db.session.query(PrintJob.printer_id, func.count(PrintJob.id)).group_by(PrintJob.printer_id).all())
    printers = Printer.query.order_by(Printer.name).all()
    for printer in printers:
        printer.job_count = job_counts.get(printer.id, 0)
    
    color_settings = ColorSetting.query.order_by(ColorSetting.sort_order).all()
    
    all_settings = {s.key: s.value for s in Setting.query.all()}
    
    import_settings = {
        'xml_url': all_settings.get('xml_url', ''),
        'sync_interval_minutes': all_settings.get('sync_interval_minutes', '60'),
        'salesdrive_domain': all_settings.get('salesdrive_domain', ''),
        'salesdrive_login': all_settings.get('salesdrive_login', ''),
        'salesdrive_password': all_settings.get('salesdrive_password', ''),
    }
    
    return render_template('settings.html',
                           printers=printers,
                           color_settings=color_settings,
                           import_settings=import_settings)


@bp.route('/sync-now', methods=['POST'])
def sync_now():
    try:
        sync_products_from_xml()
        return jsonify({'status': 'success', 'message': 'Синхронізацію успішно завершено.'})
    except Exception as e:
        current_app.logger.error(f"Manual sync failed: {e}")
        return jsonify({'status': 'error', 'message': f'Помилка під час синхронізації: {e}'}), 500

@bp.route('/settings/reset-colors', methods=['POST'])
def reset_color_settings():
    populate_default_colors()
    flash('Налаштування кольорів скинуто до стандартних.', 'success')
    return redirect(url_for('main.settings'))

@bp.route('/settings/printers/delete/<int:id>', methods=['POST'])
def delete_printer(id):
    printer_to_delete = Printer.query.get_or_404(id)
    db.session.delete(printer_to_delete)
    db.session.commit()
    flash("Принтер видалено.", "success")
    return redirect(url_for('main.settings'))

@bp.route('/settings/printers/edit/<int:id>', methods=['GET', 'POST'])
def edit_printer(id):
    printer = Printer.query.get_or_404(id)
    if request.method == 'POST':
        if 'is_default' in request.form and not printer.is_default:
            Printer.query.filter(Printer.id != id).update({Printer.is_default: False})
        
        printer.name = request.form.get('name')
        printer.ip_address = request.form.get('ip_address')
        printer.port = int(request.form.get('port'))
        printer.label_size = request.form.get('label_size')
        printer.pause_between_jobs = int(request.form.get('pause_between_jobs', 1))
        printer.is_default = 'is_default' in request.form
        printer.is_for_sorting = 'is_for_sorting' in request.form
        printer.zpl_code_template = request.form.get('zpl_code_template')
        
        db.session.commit()
        flash("Налаштування принтера оновлено.", "success")
        return redirect(url_for('main.settings'))
    
    placeholders = get_all_placeholders()
    default_template = printer.zpl_code_template or get_default_template_for_size(printer.is_for_sorting, printer.label_size)
    return render_template('edit_printer.html', printer=printer, placeholders=placeholders, default_template=default_template)