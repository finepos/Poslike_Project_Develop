# app/printing.py
import socket
import json
import re
import logging
from datetime import datetime, timezone, timedelta

from .utils import get_default_template_for_size

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def check_printer_status(printer_ip, printer_port):
    log = logging.getLogger(__name__)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((printer_ip, int(printer_port)))
            s.send(b"~HS")
            response_raw = s.recv(1024)

        if not response_raw:
            return False, "Empty Response"
        
        response_decoded = response_raw.decode("utf-8", errors='ignore')
        clean_response = response_decoded.replace('\x02', '').replace('\x03', '')
        
        log.info(f"Printer {printer_ip}:{printer_port} cleaned response:\n{clean_response.strip()}")

        is_paused = False
        is_paper_out = False
        is_ready = False
        
        for line in clean_response.strip().split('\n'):
            clean_line = line.strip()
            if not clean_line:
                continue

            if clean_line.startswith('030,0,0'):
                is_ready = True

            parts = clean_line.split(',')
            if len(parts) >= 3:
                if parts[1] == '1':
                    is_paused = True
                if parts[2] == '1':
                    is_paper_out = True
        
        if is_paused:
            return False, "Paused"
        if is_paper_out:
            return False, "Paper Out / Alarm"
        if is_ready:
            return True, "Ready"
        
        return False, "Not Ready (Unknown Response)"

    except socket.timeout:
        log.warning(f"Printer {printer_ip}:{printer_port} - Connection timed out.")
        return False, "Offline"
    except (socket.error, ConnectionRefusedError) as e:
        log.warning(f"Printer {printer_ip}:{printer_port} - Connection error: {e}")
        return False, "Offline"
    except Exception as e:
        log.error(f"Printer {printer_ip}:{printer_port} - Unknown error on status check: {e}")
        return False, "Unknown Error"


# --- ОНОВЛЕНА ФУНКЦІЯ ---
def generate_zpl_code(printer, product, sorting_quantity=None, quantity=1, override_price=None):
    """
    ОНОВЛЕНО: Генерує ZPL-код та додає команду для друку кількох копій.
    Додано параметр override_price для друку сконвертованої ціни.
    Тепер враховує розмір етикетки для вибору шаблону.
    """
    if not product.xml_data:
        return "^XA^FDError: No XML data^FS^XZ"

    data = json.loads(product.xml_data)
    
    # Якщо передано нову ціну, оновлюємо її в словнику даних
    if override_price is not None:
        data['product_price'] = f"{override_price:.2f}"

    # --- ПОЧАТОК ЗМІН ---
    # Передаємо is_for_sorting та label_size для вибору правильного шаблону
    template = printer.zpl_code_template or get_default_template_for_size(printer.is_for_sorting, printer.label_size)
    # --- КІНЕЦЬ ЗМІН ---

    if '{product_date}' in template:
        kyiv_tz = timezone(timedelta(hours=3))
        now_kyiv = datetime.now(kyiv_tz)
        date_str = now_kyiv.strftime('%H:%M %d.%m.%Y')
        template = template.replace('{product_date}', date_str)

    template = re.sub(r'\{product_param:([^}]+)\}', lambda m: str(data.get('product_params', {}).get(m.group(1), '')), template)
    sorting_text = f'{sorting_quantity} шт.' if sorting_quantity and sorting_quantity.isdigit() else ''
    template = template.replace('{product_sorting_quantity}', sorting_text)

    for key, value in data.items():
        if key.startswith('product_') and key != 'product_params':
            template = template.replace(f'{{{key}}}', str(value or ''))

    if quantity > 1:
        if '^XZ' in template:
            template = template.replace('^XZ', f'^PQ{quantity}^XZ')
        else:
            template += f'^PQ{quantity}'

    return template


def send_zpl_to_printer(printer_ip, printer_port, zpl_code):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((printer_ip, int(printer_port)))
            s.sendall(zpl_code.encode('utf-8'))
        return True, "Завдання успішно відправлено."
    except socket.timeout:
        return False, "Помилка: Таймаут підключення до принтера."
    except Exception as e:
        return False, f"Помилка відправки: {e}"