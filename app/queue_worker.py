# app/queue_worker.py
import time
import logging
from datetime import datetime, timedelta
from sqlalchemy import func
# Не потрібен flask_app та init_worker

from .extensions import db
from .models import PrintJob, Printer
from .printing import check_printer_status, send_zpl_to_printer

printer_states = {}

def process_print_queue():
    # Контекст тепер створюється у файлі run.py
    printers_with_jobs = db.session.query(PrintJob.printer_id).distinct().all()
    printer_ids = [p[0] for p in printers_with_jobs]

    if not printer_ids:
        return

    for printer_id in printer_ids:
        process_jobs_for_printer(printer_id)

def process_jobs_for_printer(printer_id):
    log = logging.getLogger(__name__)

    if printer_id not in printer_states:
        printer_states[printer_id] = {"last_check": datetime.min, "failed_attempts": 0}
    
    state = printer_states[printer_id]
    
    if state["failed_attempts"] > 0 and datetime.now() < state["last_check"] + timedelta(seconds=30):
        # log.info(f"WORKER: Printer ID {printer_id} is on hold due to previous errors. Retrying later.")
        return

    printer = Printer.query.get(printer_id)
    if not printer:
        log.warning(f"WORKER: Printer ID {printer_id} not found. Clearing its jobs.")
        PrintJob.query.filter_by(printer_id=printer_id).delete()
        db.session.commit()
        if printer_id in printer_states:
            del printer_states[printer_id]
        return

    # log.info(f"WORKER: Checking status for printer '{printer.name}'...")
    is_ready_before, msg = check_printer_status(printer.ip_address, printer.port)
    state["last_check"] = datetime.now()

    if not is_ready_before:
        state["failed_attempts"] += 1
        # log.warning(f"WORKER: Printer '{printer.name}' is not ready. Status: {msg}. Failed attempts: {state['failed_attempts']}.")
        
        if state["failed_attempts"] >= 10:
            log.error(f"WORKER: Printer '{printer.name}' reached 10 failed attempts. Clearing its print queue.")
            PrintJob.query.filter_by(printer_id=printer_id).delete()
            db.session.commit()
            state["failed_attempts"] = 0
        return
    
    # log.info(f"WORKER: Printer '{printer.name}' is READY. Looking for jobs.")
    state["failed_attempts"] = 0
    
    job = PrintJob.query.filter_by(printer_id=printer_id).order_by(PrintJob.created_at).first()
    
    if job:
        log.info(f"WORKER: Found job ID {job.id}. Sending to printer '{printer.name}'.")
        success, send_msg = send_zpl_to_printer(printer.ip_address, printer.port, job.zpl_code)
        
        if success:
            # log.info(f"WORKER: Job {job.id} sent successfully. Verifying printer status post-print.")
            time.sleep(2)
            is_ready_after, _ = check_printer_status(printer.ip_address, printer.port)
            
            if is_ready_after:
                # log.info(f"WORKER: Printer '{printer.name}' is ready post-print. Deleting job {job.id}.")
                db.session.delete(job)
                db.session.commit()
                time.sleep(printer.pause_between_jobs)
            else:
                log.warning(f"WORKER: Printer '{printer.name}' is NOT ready post-print. Job {job.id} will be retried.")
        else:
            log.error(f"WORKER: Failed to send job {job.id}. Reason: {send_msg}. Job will be retried.")
    # else:
        # log.info(f"WORKER: Printer '{printer.name}' is ready, but no jobs found in its queue.")