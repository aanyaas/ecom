from datetime import datetime, timedelta
from flask import render_template, request, redirect, url_for, flash, send_file
from admin.admin_app import admin_bp, admin_login_required
from extensions import db

@admin_bp.route('/reports')
@admin_login_required
def admin_reports():
    return render_template('admin/reports.html')

@admin_bp.route('/reports/generate', methods=['POST'])
@admin_login_required
def admin_reports_generate():
    report_id = request.form.get('report_id')
    period = request.form.get('period')
    format_type = request.form.get('format', 'pdf')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')

    # Convert period to dates if not custom
    if period != 'custom':
        today = datetime.now().date()
        if period == 'daily':
            start_date = today.strftime('%Y-%m-%d')
            end_date = today.strftime('%Y-%m-%d')
        elif period == 'weekly':
            start_date = (today - timedelta(days=7)).strftime('%Y-%m-%d')
            end_date = today.strftime('%Y-%m-%d')
        elif period == 'monthly':
            start_date = today.replace(day=1).strftime('%Y-%m-%d')
            end_date = today.strftime('%Y-%m-%d')
        elif period == 'quarterly':
            month = (today.month - 1) // 3 * 3 + 1
            start_date = today.replace(month=month, day=1).strftime('%Y-%m-%d')
            end_date = today.strftime('%Y-%m-%d')
        elif period == 'yearly':
            start_date = today.replace(month=1, day=1).strftime('%Y-%m-%d')
            end_date = today.strftime('%Y-%m-%d')

    # For now, use the existing generator
    # We will expand sales_report.py next to handle report_id
    from sales_report import generate_advanced_report
    
    # We need a db_pool or just pass config/connection
    # Existing generate_sales_report in sales_report.py uses db_pool
    # Let's check how to get a pool or adapt the function
    
    # Simple hack: create a mock pool object that generate_sales_report expects
    class MockPool:
        def get_connection(self):
            return db.engine.raw_connection()
    
    buffer, error = generate_advanced_report(MockPool(), start_date, end_date, report_id, format_type)
    
    if error:
        flash(f'Error generating report: {error}', 'danger')
        return redirect(url_for('admin_bp.admin_reports'))
    
    ext = 'pdf' if format_type == 'pdf' else ('csv' if format_type == 'csv' else 'xlsx')
    filename = f"{report_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
    if format_type == 'pdf':
        mimetype = 'application/pdf'
    elif format_type == 'csv':
        mimetype = 'text/csv'
    else:
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    
    return send_file(
        buffer, # type: ignore
        as_attachment=True,
        download_name=filename,
        mimetype=mimetype
    )
