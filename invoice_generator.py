from io import BytesIO
import mysql.connector
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import mm
import os
from datetime import datetime
import json
from decimal import Decimal
from contextlib import contextmanager
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, 'static', 'fonts')

for _fname, _ffile in [('Roboto','Roboto-Regular.ttf'),('Roboto-Bold','Roboto-Bold.ttf'),('Roboto-Light','Roboto-Light.ttf')]:
    try:
        _fp = os.path.join(FONT_DIR, _ffile)
        if os.path.exists(_fp):
            pdfmetrics.registerFont(TTFont(_fname, _fp))
    except Exception as _e:
        print(f"Font registration error {_fname}: {_e}")


@contextmanager
def get_db_connection(db_pool):
    conn = None
    try:
        conn = db_pool.get_connection()
        yield conn
    except Exception as e:
        print(f"Database error: {str(e)}")
        raise
    finally:
        if conn:
            conn.close()


def generate_invoice_number(db_pool):
    try:
        with get_db_connection(db_pool) as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT MAX(id) as last_id FROM orders")
            result = cursor.fetchone()
            last_id = result['last_id'] if result['last_id'] else 0
            return f"INV{datetime.now().strftime('%y%m')}{last_id + 1:04d}"
    except Exception as e:
        print(f"Error generating invoice number: {str(e)}")
        return f"INV{datetime.now().strftime('%y%m%d%H%M%S')}"


def generate_invoice_number_new(conn):
    """Generate invoice number: W2627-1"""
    try:
        cursor = conn.cursor(dictionary=True)
        now = datetime.now()
        if now.month >= 4:
            fy = f"{now.year}-{now.year+1}"
            fy_short = f"{now.year%100}{(now.year+1)%100}"
        else:
            fy = f"{now.year-1}-{now.year}"
            fy_short = f"{(now.year-1)%100}{now.year%100}"

        cursor.execute("SELECT * FROM invoice_numbers WHERE financial_year = %s", (fy,))
        rec = cursor.fetchone()
        if rec:
            seq = rec['sequence'] + 1
            cursor.execute("UPDATE invoice_numbers SET sequence=%s WHERE financial_year=%s", (seq, fy))
        else:
            seq = 1
            cursor.execute("INSERT INTO invoice_numbers (financial_year, sequence) VALUES (%s,%s)", (fy, seq))
        conn.commit()
        return f"W{fy_short}-{seq}"
    except Exception as e:
        print(f"Error generating invoice number: {str(e)}")
        return f"W{datetime.now().strftime('%y%m%d%H%M%S')}"


def number_to_words_indian(number):
    """Simple converter for numbers to words (Indian Rupees)"""
    def _convert(n):
        units = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"]
        teens = ["Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
        tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
        
        if n == 0: return ""
        elif n < 10: return units[n]
        elif n < 20: return teens[n-10]
        elif n < 100: return tens[n//10] + (" " + units[n%10] if n%10 != 0 else "")
        elif n < 1000: return units[n//100] + " Hundred" + (" and " + _convert(n%100) if n%100 != 0 else "")
        elif n < 100000: return _convert(n//1000) + " Thousand" + (" " + _convert(n%1000) if n%1000 != 0 else "")
        elif n < 10000000: return _convert(n//100000) + " Lakh" + (" " + _convert(n%100000) if n%100000 != 0 else "")
        else: return _convert(n//10000000) + " Crore" + (" " + _convert(n%10000000) if n%10000000 != 0 else "")

    if number == 0: return "Zero Rupees Only"
    
    integer_part = int(number)
    fractional_part = round((number - integer_part) * 100)
    
    words = _convert(integer_part) + " Rupees"
    if fractional_part > 0:
        words += " and " + _convert(fractional_part) + " Paise"
    
    return words + " Only"

def generate_invoice_pdf(order_id, conn, app, invoice_number=None):
    """Generate a premium PDF invoice."""
    _own_conn = None
    try:
        if conn is None or not conn.is_connected():
            _own_conn = mysql.connector.connect(
                host=os.getenv('DB_HOST','localhost'),
                port=int(os.getenv('DB_PORT', 3309)),
                user=os.getenv('DB_USER','root'),
                password=os.getenv('DB_PASSWORD',''),
                database=os.getenv('DB_NAME',''),
                ssl_disabled=True, use_pure=True,
                connection_timeout=10, autocommit=True,
            )
            conn = _own_conn
        cursor = conn.cursor(dictionary=True)

        # Company
        cursor.execute("SELECT * FROM company_info LIMIT 1")
        company = cursor.fetchone()
        if not company:
            return None, "Company details not configured"

        # Order
        cursor.execute("""
            SELECT o.*, u.username, u.email,
                DATE_FORMAT(o.order_date,  '%d-%m-%Y %h:%i %p') as fmt_order_date,
                DATE_FORMAT(o.invoice_date,'%d-%m-%Y %h:%i %p') as fmt_invoice_date
            FROM orders o JOIN users u ON o.user_id=u.id WHERE o.id=%s
        """, (order_id,))
        order = cursor.fetchone()
        if not order:
            return None, 'Order not found'

        if not invoice_number:
            invoice_number = order.get('invoice_number') or generate_invoice_number_new(conn)

        if not order.get('invoice_number'):
            cursor.execute("UPDATE orders SET invoice_number=%s, invoice_date=NOW() WHERE id=%s",
                           (invoice_number, order_id))
            conn.commit()
            cursor.execute("""
                SELECT o.*, u.username, u.email,
                    DATE_FORMAT(o.order_date,  '%d-%m-%Y %h:%i %p') as fmt_order_date,
                    DATE_FORMAT(o.invoice_date,'%d-%m-%Y %h:%i %p') as fmt_invoice_date
                FROM orders o JOIN users u ON o.user_id=u.id WHERE o.id=%s
            """, (order_id,))
            order = cursor.fetchone()

        # Items
        cursor.execute("""
            SELECT oi.*, p.hsn_code, p.gst_rate, p.sku
            FROM order_items oi LEFT JOIN products p ON oi.product_id=p.id
            WHERE oi.order_id=%s
        """, (order_id,))
        items = cursor.fetchall()
        gst_breakdown = calculate_gst_breakdown(order_id, conn)

        # ── Layout constants ──────────────────────────────────────────────
        buffer  = BytesIO()
        PAGE_W  = 186 * mm   # A4 210mm − 2×12mm margins
        doc     = SimpleDocTemplate(buffer, pagesize=A4,
                                    leftMargin=12*mm, rightMargin=12*mm,
                                    topMargin=12*mm,  bottomMargin=12*mm)

        PRIMARY  = colors.HexColor('#1a3c6e')
        ACCENT   = colors.HexColor('#4e73df')
        LIGHT_BG = colors.HexColor('#f4f6fb')
        BORDER   = colors.HexColor('#c8d0e7')
        DARK_TXT = colors.HexColor('#2d2d2d')
        WHITE    = colors.white

        def _sty(name, **kw):
            # Apply defaults if not provided in kw
            for k, v in {'fontSize': 9, 'textColor': DARK_TXT, 'leading': 13}.items():
                if k not in kw:
                    kw[k] = v
            return ParagraphStyle(name=name, **kw)

        sty = {
            'title':   _sty('i_title',  fontName='Roboto-Bold', fontSize=22, textColor=PRIMARY, alignment=2),
            'label':   _sty('i_label',  fontName='Roboto-Bold', fontSize=8,  textColor=ACCENT),
            'addr':    _sty('i_addr',   fontName='Roboto',      fontSize=8, leading=12),
            'th':      _sty('i_th',     fontName='Roboto-Bold', fontSize=8,  textColor=WHITE, alignment=1),
            'td_c':    _sty('i_tdc',    fontName='Roboto',      fontSize=8, leading=11, alignment=1),
            'td_l':    _sty('i_tdl',    fontName='Roboto',      fontSize=8, leading=11, alignment=0),
            'td_r':    _sty('i_tdr',    fontName='Roboto',      fontSize=8, leading=11, alignment=2),
            'tot_lbl': _sty('i_totlbl', fontName='Roboto-Bold', fontSize=9,  alignment=2),
            'tot_val': _sty('i_totval', fontName='Roboto-Bold', fontSize=9,  textColor=PRIMARY, alignment=2),
            'gtotal_lbl': _sty('i_glbl', fontName='Roboto-Bold', fontSize=10, textColor=WHITE, alignment=2),
            'gtotal':  _sty('i_grand',  fontName='Roboto-Bold', fontSize=12, textColor=WHITE,  alignment=2),
            'sign':    _sty('i_sign',   fontName='Roboto',      fontSize=8, alignment=1),
            'info_lbl': _sty('i_infol', fontName='Roboto-Bold', fontSize=7, textColor=ACCENT),
            'info_val': _sty('i_infov', fontName='Roboto', fontSize=9, textColor=PRIMARY),
        }

        def P(txt, s): return Paragraph(str(txt), sty[s])  # type: ignore

        elements = []

        # ── HEADER ────────────────────────────────────────────────────────
        logo_path = os.path.join(BASE_DIR, 'static', 'img', 'logo.png')
        if os.path.exists(logo_path):
            try:
                from PIL import Image as PILImg
                with PILImg.open(logo_path) as im:
                    iw, ih = im.size
                lh = 18 * mm
                lw = min(lh * iw / ih, 60 * mm)
            except Exception:
                lw, lh = 45 * mm, 15 * mm
            left_cell = Image(logo_path, width=lw, height=lh)
        else:
            left_cell = P(f"<b>{company.get('company_name','')}</b>", 'addr')

        hdr = Table([[left_cell, P("TAX INVOICE", 'title')]],
                    colWidths=[PAGE_W * 0.5, PAGE_W * 0.5])
        hdr.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN',  (1, 0), (1,  0),  'RIGHT'),
        ]))
        elements += [hdr, Spacer(1, 10)]

        # ── INFO BAR ─────────────────────────────────────────────────────
        bw = PAGE_W / 4
        bar_data = [
            [P("INVOICE NO", 'info_lbl'), P("INVOICE DATE", 'info_lbl'), P("ORDER ID", 'info_lbl'), P("ORDER DATE", 'info_lbl')],
            [P(invoice_number, 'info_val'), P(order.get('fmt_invoice_date') or '—', 'info_val'), P(f"#{order_id}", 'info_val'), P(order.get('fmt_order_date') or '—', 'info_val')]
        ]
        bar = Table(bar_data, colWidths=[bw] * 4)
        bar.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,-1), LIGHT_BG),
            ('LINEABOVE',     (0,0),(-1,-1), 1, PRIMARY),
            ('LINEBELOW',     (0,0),(-1,-1), 1, BORDER),
            ('LEFTPADDING',   (0,0),(-1,-1), 10),
            ('RIGHTPADDING',  (0,0),(-1,-1), 10),
            ('TOPPADDING',    (0,0),(-1,0),  8),
            ('BOTTOMPADDING', (0,0),(-1,0),  0),
            ('TOPPADDING',    (0,1),(-1,1),  2),
            ('BOTTOMPADDING', (0,1),(-1,1),  10),
            ('INNERGRID',     (0,0),(-1,-1), 0.1, BORDER),
        ]))
        elements += [bar, Spacer(1, 15)]

        # ── ADDRESSES ────────────────────────────────────────────────────
        def fmt_addr(raw):
            try:
                a = json.loads(raw) if isinstance(raw, str) else (raw or {})
                lines = [
                    f"<b>{a.get('first_name','')} {a.get('last_name','')}</b>",
                ]
                
                addr_type = str(a.get('address_type', '')).lower()
                is_company = (addr_type == 'company') or (not addr_type and a.get('company_name')) 
                
                if is_company and a.get('company_name'):
                    lines.append(f"<b>{a.get('company_name')}</b>")
                
                gst = a.get('gst_number') or a.get('gstin')
                if is_company and gst:
                    lines.append(f"GSTIN: {gst}")
                
                lines.extend([
                    a.get('address1', ''),
                    f"{a.get('address2','')} {a.get('city','')}".strip(),
                    f"{a.get('state','')} - {a.get('zip_code','')}",
                    f"Phone: {a.get('phone','')}",
                ])
                return "<br/>".join(l for l in lines if l.strip(" -:"))
            except Exception:
                return "N/A"

        seller = (f"<b>{company.get('company_name','')}</b><br/>"
                  f"{company.get('address','')}, {company.get('city','')}<br/>"
                  f"{company.get('state','')} - {company.get('pincode','')}<br/>"
                  f"GSTIN: {company.get('gstin','')}<br/>PAN: {company.get('pan','')}<br/>State/UT Code: {company.get('state_code','')}")

        qr_cell = ""
        try:
            import qrcode
            inv_dir = os.path.join(app.static_folder, 'invoices')
            os.makedirs(inv_dir, exist_ok=True)
            qr_path = os.path.join(inv_dir, f'qr_{order_id}.png')
            
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=2,
            )
            fmt_inv_date = order.get('fmt_invoice_date') or '—'
            fmt_ord_date = order.get('fmt_order_date') or '—'
            qr_data = f"Invoice: {invoice_number}, Date: {fmt_inv_date} | Order: {order_id}, Date: {fmt_ord_date}"
            qr.add_data(qr_data)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            with open(qr_path, 'wb') as f:
                img.save(f)
            
            qr_cell = Image(qr_path, width=35*mm, height=35*mm)
        except Exception as e:
            print("QR Error:", e)

        ac = PAGE_W / 3
        addr_hdr  = Table([[P("SOLD BY",'label'), P("DELIVERY ADDRESS",'label'), ""]], colWidths=[ac]*3)
        addr_hdr.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), LIGHT_BG),
            ('LEFTPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ]))
        
        addr_body = Table([[P(seller,'addr'),
                            P(fmt_addr(order.get('shipping_address','{}')), 'addr'),
                            qr_cell]],
                          colWidths=[ac]*3)
        addr_body.setStyle(TableStyle([
            ('VALIGN',        (0,0),(-1,-1), 'TOP'),
            ('TOPPADDING',    (0,0),(-1,-1), 6),
            ('BOTTOMPADDING', (0,0),(-1,-1), 10),
            ('LEFTPADDING',   (0,0),(-1,-1), 5),
            ('ALIGN',         (2,0),(2,0), 'RIGHT'),
        ]))
        elements += [addr_hdr, addr_body, Spacer(1, 1)]

        # ── ITEMS TABLE ───────────────────────────────────────────────────
        is_intra = gst_breakdown.get('is_intra_state', True)

        # widths must sum to PAGE_W = 186 mm
        if is_intra:
            cw = [7, 54, 17, 10, 20, 22, 19, 19, 18]  # 9 cols = 186
        else:
            cw = [7, 72, 17, 10, 20, 23, 37, 0,  0]   # 7 real cols, pad rest = 186

        cw_mm = [c * mm for c in cw]

        if is_intra:
            hdrs = ['#','PRODUCT DESCRIPTION','HSN','QTY','UNIT PRICE','TAXABLE','CGST','SGST','TOTAL']
        else:
            hdrs = ['#','PRODUCT DESCRIPTION','HSN','QTY','UNIT PRICE','TAXABLE','IGST','','TOTAL']

        rows = [[P(h, 'th') for h in hdrs]]

        tot_taxable = Decimal(0)
        tot_gst     = Decimal(0)
        for idx, item in enumerate(items, 1):
            price = Decimal(str(item.get('price') or item.get('unit_price') or 0))
            qty   = Decimal(str(item['quantity']))
            rate  = Decimal(str(item.get('gst_rate') or 18))
            
            # Use saved database values if they exist, otherwise fallback to standard calculation!
            if item.get('taxable_value') is not None and float(item.get('taxable_value')) > 0:
                tax = Decimal(str(item.get('taxable_value')))
                cgst_saved = Decimal(str(item.get('cgst_amount') or 0))
                sgst_saved = Decimal(str(item.get('sgst_amount') or 0))
                igst_saved = Decimal(str(item.get('igst_amount') or 0))
                gst = cgst_saved + sgst_saved + igst_saved
                total = tax + gst
            else:
                total = price * qty
                tax   = total / (1 + rate / 100)
                gst   = total - tax
                
            tot_taxable += tax
            tot_gst     += gst

            prod = P(
                f"<b>{item['product_name']}</b><br/>"
                f"<font size='7' color='#555555'>SKU: {item.get('sku') or 'N/A'}</font>",
                'td_l'
            )

            if is_intra:
                half = gst / 2
                row = [
                    P(str(idx), 'td_c'),
                    prod,
                    P(item.get('hsn_code') or '-', 'td_c'),
                    P(str(item['quantity']), 'td_c'),
                    P(f"₹{price:,.2f}", 'td_r'),
                    P(f"₹{tax:,.2f}", 'td_r'),
                    P(f"₹{half:,.2f}<br/><font size='6'>( {rate/2}% )</font>", 'td_r'),
                    P(f"₹{half:,.2f}<br/><font size='6'>( {rate/2}% )</font>", 'td_r'),
                    P(f"₹{total:,.2f}", 'td_r'),
                ]
            else:
                row = [
                    P(str(idx), 'td_c'),
                    prod,
                    P(item.get('hsn_code') or '-', 'td_c'),
                    P(str(item['quantity']), 'td_c'),
                    P(f"₹{price:,.2f}", 'td_r'),
                    P(f"₹{tax:,.2f}", 'td_r'),
                    P(f"₹{gst:,.2f}<br/><font size='6'>( {rate}% )</font>", 'td_r'),
                    P('', 'td_c'),
                    P(f"₹{total:,.2f}", 'td_r'),
                ]
            rows.append(row)

        tot_qty = sum(Decimal(str(item['quantity'])) for item in items)
        if is_intra:
            total_row = [
                P('', 'td_c'),
                P('<b>TOTAL</b>', 'td_r'),
                P('', 'td_c'),
                P(f"<b>{int(tot_qty)}</b>", 'td_c'),
                P('', 'td_c'),
                P(f"<b>₹{tot_taxable:,.2f}</b>", 'td_r'),
                P(f"<b>₹{tot_gst/2:,.2f}</b>", 'td_r'),
                P(f"<b>₹{tot_gst/2:,.2f}</b>", 'td_r'),
                P(f"<b>₹{tot_taxable + tot_gst:,.2f}</b>", 'td_r'),
            ]
        else:
            total_row = [
                P('', 'td_c'),
                P('<b>TOTAL</b>', 'td_r'),
                P('', 'td_c'),
                P(f"<b>{int(tot_qty)}</b>", 'td_c'),
                P('', 'td_c'),
                P(f"<b>₹{tot_taxable:,.2f}</b>", 'td_r'),
                P(f"<b>₹{tot_gst:,.2f}</b>", 'td_r'),
                P('', 'td_c'),
                P(f"<b>₹{tot_taxable + tot_gst:,.2f}</b>", 'td_r'),
            ]
        rows.append(total_row)

        tbl = Table(rows, colWidths=cw_mm, repeatRows=1)
        tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,0),  PRIMARY),
            ('TEXTCOLOR',     (0,0),(-1,0),  WHITE),
            ('ROWBACKGROUNDS',(0,1),(-1,-1), [WHITE, LIGHT_BG]),
            ('GRID',          (0,0),(-1,-1), 0.1, colors.grey),
            ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
            ('TOPPADDING',    (0,0),(-1,-1), 6),
            ('BOTTOMPADDING', (0,0),(-1,-1), 6),
            ('LEFTPADDING',   (0,0),(-1,-1), 4),
            ('RIGHTPADDING',  (0,0),(-1,-1), 4),
        ]))
        elements += [tbl, Spacer(1, 5)]

        # ── TOTALS ────────────────────────────────────────────────────────
        shipping = Decimal(str(order.get('shipping_charge') or 0))
        discount = Decimal(str(order.get('discount_amount') or 0))
        grand    = Decimal(str(order.get('total_amount')   or 0))

        tot_rows = []
        if discount > 0:
            tot_rows.append([P("Discount:", 'tot_lbl'), P(f"-₹{discount:,.2f}", 'tot_val')])
        tot_rows.append([P("Shipping Charges:", 'tot_lbl'), P(f"₹{shipping:,.2f}", 'tot_val')])

        tot_tbl = Table(tot_rows, colWidths=[80*mm, 40*mm])
        tot_tbl.setStyle(TableStyle([
            ('ALIGN',         (0,0),(-1,-1), 'RIGHT'),
            ('TOPPADDING',    (0,0),(-1,-1), 2),
            ('BOTTOMPADDING', (0,0),(-1,-1), 2),
        ]))

        grand_tbl = Table(
            [[P("GRAND TOTAL", 'gtotal_lbl'), P(f"₹{grand:,.2f}", 'gtotal')]],
            colWidths=[80*mm, 40*mm]
        )
        grand_tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0,0),(-1,-1), PRIMARY),
            ('TOPPADDING',    (0,0),(-1,-1), 8),
            ('BOTTOMPADDING', (0,0),(-1,-1), 8),
            ('LEFTPADDING',   (0,0),(-1,-1), 8),
            ('RIGHTPADDING',  (0,0),(-1,-1), 8),
        ]))

        amount_in_words = number_to_words_indian(float(grand))
        
        summary = Table(
            [["", tot_tbl], 
             ["", grand_tbl]],
            colWidths=[PAGE_W - 120*mm, 120*mm]
        )
        summary.setStyle(TableStyle([
            ('VALIGN', (1,0), (1,1), 'TOP'),
        ]))
        elements += [summary, Spacer(1, 15)]
        
        elements += [P(f"<b>Amount in Words:</b>{amount_in_words}", 'addr'), Spacer(1, 3)]

        # ── FOOTER: payment + terms ───────────────────────────────────────
        order_status = str(order.get('status', '')).lower()
        payment_status = str(order.get('payment_status', '')).lower()
        payment_method = str(order.get('payment_method', '—')).upper()
        
        if payment_status in ('completed', 'success', 'paid'):
            status_txt = 'Paid'
        else:
            status_txt = 'Pending'
                
        payment_info = f"Mode of payment: {payment_method}<br/>Status: {status_txt}"
        
        if payment_method != 'COD' and order.get('transaction_id'):
            payment_info += f"<br/>Payment Transaction ID: {order.get('transaction_id')}"
        elif payment_method != 'COD' and order.get('razorpay_payment_id'):
            payment_info += f"<br/>Payment Transaction ID: {order.get('razorpay_payment_id')}"

        hw = PAGE_W / 2
        footer = Table([
            [P("<b>PAYMENT INFORMATION</b>", 'label'), P("<b>TERMS &amp; CONDITIONS</b>", 'label')],
            [
                P(payment_info, 'addr'),
                P("1. Goods once sold will not be taken back.<br/>"
                  "2. Computer-generated invoice; no physical signature required.<br/>"
                  "3. Subject to jurisdiction of local courts.", 'addr'),
            ],
        ], colWidths=[hw, hw])
        footer.setStyle(TableStyle([
            ('VALIGN',        (0,0),(-1,-1), 'TOP'),
            ('TOPPADDING',    (0,0),(-1,-1), 5),
            ('BOTTOMPADDING', (0,0),(-1,-1), 5),
            ('BACKGROUND',    (0,0), (-1,0), LIGHT_BG),
        ]))
        elements += [footer, Spacer(1, 28)]

        # ── SIGNATURE ─────────────────────────────────────────────────────
        sig = Table(
            [["", P(f"For <b>{company['company_name']}</b><br/><br/><br/><br/>Authorized Signatory", 'sign')]],
            colWidths=[PAGE_W * 0.6, PAGE_W * 0.4]
        )
        sig.setStyle(TableStyle([
            ('ALIGN',  (1,0),(1,0), 'CENTER'),
            ('VALIGN', (0,0),(-1,-1), 'BOTTOM'),
        ]))
        elements.append(sig)

        # ── BUILD ─────────────────────────────────────────────────────────
        doc.build(elements)  # type: ignore

        inv_dir = os.path.join(app.static_folder, 'invoices')
        os.makedirs(inv_dir, exist_ok=True)
        with open(os.path.join(inv_dir, f'invoice_{order_id}.pdf'), 'wb') as f:
            f.write(buffer.getvalue())

        buffer.seek(0)
        if _own_conn:
            try: _own_conn.close()
            except: pass
        return buffer, None

    except Exception as e:
        import traceback; traceback.print_exc()
        return None, str(e)



from reportlab.platypus import PageBreak
def generate_bulk_invoices_pdf(order_ids, conn, app):
    """Generate a premium PDF with multiple invoices."""
    _own_conn = None
    try:
        if conn is None or not conn.is_connected():
            _own_conn = mysql.connector.connect(
                host=os.getenv('DB_HOST','localhost'),
                port=int(os.getenv('DB_PORT', 3309)),
                user=os.getenv('DB_USER','root'),
                password=os.getenv('DB_PASSWORD',''),
                database=os.getenv('DB_NAME',''),
                ssl_disabled=True, use_pure=True,
                connection_timeout=10, autocommit=True,
            )
            conn = _own_conn
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM company_info LIMIT 1")
        company = cursor.fetchone()
        if not company:
            return None, "Company details not configured"

        buffer  = BytesIO()
        PAGE_W  = 186 * mm
        doc     = SimpleDocTemplate(buffer, pagesize=A4,
                                    leftMargin=12*mm, rightMargin=12*mm,
                                    topMargin=12*mm,  bottomMargin=12*mm)
        all_elements = []
        
        for idx, order_id in enumerate(order_ids):
            # Order
            cursor.execute("""
                SELECT o.*, u.username, u.email,
                    DATE_FORMAT(o.order_date,  '%d-%m-%Y %h:%i %p') as fmt_order_date,
                    DATE_FORMAT(o.invoice_date,'%d-%m-%Y %h:%i %p') as fmt_invoice_date
                FROM orders o JOIN users u ON o.user_id=u.id WHERE o.id=%s
            """, (order_id,))
            order = cursor.fetchone()
            if not order:
                return None, 'Order not found'

            invoice_number = order.get('invoice_number') or generate_invoice_number_new(conn)

            if not order.get('invoice_number'):
                cursor.execute("UPDATE orders SET invoice_number=%s, invoice_date=NOW() WHERE id=%s",
                               (invoice_number, order_id))
                conn.commit()
                cursor.execute("""
                    SELECT o.*, u.username, u.email,
                        DATE_FORMAT(o.order_date,  '%d-%m-%Y %h:%i %p') as fmt_order_date,
                        DATE_FORMAT(o.invoice_date,'%d-%m-%Y %h:%i %p') as fmt_invoice_date
                    FROM orders o JOIN users u ON o.user_id=u.id WHERE o.id=%s
                """, (order_id,))
                order = cursor.fetchone()

            # Items
            cursor.execute("""
                SELECT oi.*, p.hsn_code, p.gst_rate, p.sku
                FROM order_items oi LEFT JOIN products p ON oi.product_id=p.id
                WHERE oi.order_id=%s
            """, (order_id,))
            items = cursor.fetchall()
            gst_breakdown = calculate_gst_breakdown(order_id, conn)

            PRIMARY  = colors.HexColor('#1a3c6e')
            ACCENT   = colors.HexColor('#4e73df')
            LIGHT_BG = colors.HexColor('#f4f6fb')
            BORDER   = colors.HexColor('#c8d0e7')
            DARK_TXT = colors.HexColor('#2d2d2d')
            WHITE    = colors.white

            def _sty(name, **kw):
                # Apply defaults if not provided in kw
                for k, v in {'fontSize': 9, 'textColor': DARK_TXT, 'leading': 13}.items():
                    if k not in kw:
                        kw[k] = v
                return ParagraphStyle(name=name, **kw)

            sty = {
                'title':   _sty('i_title',  fontName='Roboto-Bold', fontSize=22, textColor=PRIMARY, alignment=2),
                'label':   _sty('i_label',  fontName='Roboto-Bold', fontSize=8,  textColor=ACCENT),
                'addr':    _sty('i_addr',   fontName='Roboto',      fontSize=8, leading=12),
                'th':      _sty('i_th',     fontName='Roboto-Bold', fontSize=8,  textColor=WHITE, alignment=1),
                'td_c':    _sty('i_tdc',    fontName='Roboto',      fontSize=8, leading=11, alignment=1),
                'td_l':    _sty('i_tdl',    fontName='Roboto',      fontSize=8, leading=11, alignment=0),
                'td_r':    _sty('i_tdr',    fontName='Roboto',      fontSize=8, leading=11, alignment=2),
                'tot_lbl': _sty('i_totlbl', fontName='Roboto-Bold', fontSize=9,  alignment=2),
                'tot_val': _sty('i_totval', fontName='Roboto-Bold', fontSize=9,  textColor=PRIMARY, alignment=2),
                'gtotal_lbl': _sty('i_glbl', fontName='Roboto-Bold', fontSize=10, textColor=WHITE, alignment=2),
                'gtotal':  _sty('i_grand',  fontName='Roboto-Bold', fontSize=12, textColor=WHITE,  alignment=2),
                'sign':    _sty('i_sign',   fontName='Roboto',      fontSize=8, alignment=1),
                'info_lbl': _sty('i_infol', fontName='Roboto-Bold', fontSize=7, textColor=ACCENT),
                'info_val': _sty('i_infov', fontName='Roboto', fontSize=9, textColor=PRIMARY),
            }

            def P(txt, s): return Paragraph(str(txt), sty[s])  # type: ignore

            if idx > 0:
                all_elements.append(PageBreak())  # type: ignore

            elements = []

            # ── HEADER ────────────────────────────────────────────────────────
            logo_path = os.path.join(BASE_DIR, 'static', 'img', 'logo.png')
            if os.path.exists(logo_path):
                try:
                    from PIL import Image as PILImg
                    with PILImg.open(logo_path) as im:
                        iw, ih = im.size
                    lh = 18 * mm
                    lw = min(lh * iw / ih, 60 * mm)
                except Exception:
                    lw, lh = 45 * mm, 15 * mm
                left_cell = Image(logo_path, width=lw, height=lh)
            else:
                left_cell = P(f"<b>{company.get('company_name','')}</b>", 'addr')

            hdr = Table([[left_cell, P("TAX INVOICE", 'title')]],
                        colWidths=[PAGE_W * 0.5, PAGE_W * 0.5])
            hdr.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN',  (1, 0), (1,  0),  'RIGHT'),
            ]))
            elements += [hdr, Spacer(1, 10)]

            # ── INFO BAR ─────────────────────────────────────────────────────
            bw = PAGE_W / 4
            bar_data = [
                [P("INVOICE NO", 'info_lbl'), P("INVOICE DATE", 'info_lbl'), P("ORDER ID", 'info_lbl'), P("ORDER DATE", 'info_lbl')],
                [P(invoice_number, 'info_val'), P(order.get('fmt_invoice_date') or '—', 'info_val'), P(f"#{order_id}", 'info_val'), P(order.get('fmt_order_date') or '—', 'info_val')]
            ]
            bar = Table(bar_data, colWidths=[bw] * 4)
            bar.setStyle(TableStyle([
                ('BACKGROUND',    (0,0),(-1,-1), LIGHT_BG),
                ('LINEABOVE',     (0,0),(-1,-1), 1, PRIMARY),
                ('LINEBELOW',     (0,0),(-1,-1), 1, BORDER),
                ('LEFTPADDING',   (0,0),(-1,-1), 10),
                ('RIGHTPADDING',  (0,0),(-1,-1), 10),
                ('TOPPADDING',    (0,0),(-1,0),  8),
                ('BOTTOMPADDING', (0,0),(-1,0),  0),
                ('TOPPADDING',    (0,1),(-1,1),  2),
                ('BOTTOMPADDING', (0,1),(-1,1),  10),
                ('INNERGRID',     (0,0),(-1,-1), 0.1, BORDER),
            ]))
            elements += [bar, Spacer(1, 15)]

            # ── ADDRESSES ────────────────────────────────────────────────────
            def fmt_addr(raw):
                try:
                    a = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    lines = [
                        f"<b>{a.get('first_name','')} {a.get('last_name','')}</b>",
                    ]
                
                    addr_type = str(a.get('address_type', '')).lower()
                    is_company = (addr_type == 'company') or (not addr_type and a.get('company_name')) 
                
                    if is_company and a.get('company_name'):
                        lines.append(f"<b>{a.get('company_name')}</b>")
                
                    gst = a.get('gst_number') or a.get('gstin')
                    if is_company and gst:
                        lines.append(f"GSTIN: {gst}")
                
                    lines.extend([
                        a.get('address1', ''),
                        f"{a.get('address2','')} {a.get('city','')}".strip(),
                        f"{a.get('state','')} - {a.get('zip_code','')}",
                        f"Phone: {a.get('phone','')}",
                    ])
                    return "<br/>".join(l for l in lines if l.strip(" -:"))
                except Exception:
                    return "N/A"

            seller = (f"<b>{company.get('company_name','')}</b><br/>"
                      f"{company.get('address','')}, {company.get('city','')}<br/>"
                      f"{company.get('state','')} - {company.get('pincode','')}<br/>"
                      f"GSTIN: {company.get('gstin','')}<br/>PAN: {company.get('pan','')}<br/>State/UT Code: {company.get('state_code','')}")

            qr_cell = ""
            try:
                import qrcode
                inv_dir = os.path.join(app.static_folder, 'invoices')
                os.makedirs(inv_dir, exist_ok=True)
                qr_path = os.path.join(inv_dir, f'qr_{order_id}.png')
            
                qr = qrcode.QRCode(
                    version=None,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=10,
                    border=2,
                )
                fmt_inv_date = order.get('fmt_invoice_date') or '—'
                fmt_ord_date = order.get('fmt_order_date') or '—'
                qr_data = f"Invoice: {invoice_number}, Date: {fmt_inv_date} | Order: {order_id}, Date: {fmt_ord_date}"
                qr.add_data(qr_data)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white")
                with open(qr_path, 'wb') as f:
                    img.save(f)
            
                qr_cell = Image(qr_path, width=35*mm, height=35*mm)
            except Exception as e:
                print("QR Error:", e)

            ac = PAGE_W / 3
            addr_hdr  = Table([[P("SOLD BY",'label'), P("DELIVERY ADDRESS",'label'), ""]], colWidths=[ac]*3)
            addr_hdr.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,-1), LIGHT_BG),
                ('LEFTPADDING', (0,0), (-1,-1), 5),
                ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ]))
        
            addr_body = Table([[P(seller,'addr'),
                                P(fmt_addr(order.get('shipping_address','{}')), 'addr'),
                                qr_cell]],
                              colWidths=[ac]*3)
            addr_body.setStyle(TableStyle([
                ('VALIGN',        (0,0),(-1,-1), 'TOP'),
                ('TOPPADDING',    (0,0),(-1,-1), 6),
                ('BOTTOMPADDING', (0,0),(-1,-1), 10),
                ('LEFTPADDING',   (0,0),(-1,-1), 5),
                ('ALIGN',         (2,0),(2,0), 'RIGHT'),
            ]))
            elements += [addr_hdr, addr_body, Spacer(1, 1)]

            # ── ITEMS TABLE ───────────────────────────────────────────────────
            is_intra = gst_breakdown.get('is_intra_state', True)

            # widths must sum to PAGE_W = 186 mm
            if is_intra:
                cw = [7, 54, 17, 10, 20, 22, 19, 19, 18]  # 9 cols = 186
            else:
                cw = [7, 72, 17, 10, 20, 23, 37, 0,  0]   # 7 real cols, pad rest = 186

            cw_mm = [c * mm for c in cw]

            if is_intra:
                hdrs = ['#','PRODUCT DESCRIPTION','HSN','QTY','UNIT PRICE','TAXABLE','CGST','SGST','TOTAL']
            else:
                hdrs = ['#','PRODUCT DESCRIPTION','HSN','QTY','UNIT PRICE','TAXABLE','IGST','','TOTAL']

            rows = [[P(h, 'th') for h in hdrs]]

            tot_taxable = Decimal(0)
            tot_gst     = Decimal(0)
            for idx, item in enumerate(items, 1):
                price = Decimal(str(item.get('price') or item.get('unit_price') or 0))
                qty   = Decimal(str(item['quantity']))
                rate  = Decimal(str(item.get('gst_rate') or 18))
            
                # Use saved database values if they exist, otherwise fallback to standard calculation!
                if item.get('taxable_value') is not None and float(item.get('taxable_value')) > 0:
                    tax = Decimal(str(item.get('taxable_value')))
                    cgst_saved = Decimal(str(item.get('cgst_amount') or 0))
                    sgst_saved = Decimal(str(item.get('sgst_amount') or 0))
                    igst_saved = Decimal(str(item.get('igst_amount') or 0))
                    gst = cgst_saved + sgst_saved + igst_saved
                    total = tax + gst
                else:
                    total = price * qty
                    tax   = total / (1 + rate / 100)
                    gst   = total - tax
                
                tot_taxable += tax
                tot_gst     += gst

                prod = P(
                    f"<b>{item['product_name']}</b><br/>"
                    f"<font size='7' color='#555555'>SKU: {item.get('sku') or 'N/A'}</font>",
                    'td_l'
                )

                if is_intra:
                    half = gst / 2
                    row = [
                        P(str(idx), 'td_c'),
                        prod,
                        P(item.get('hsn_code') or '-', 'td_c'),
                        P(str(item['quantity']), 'td_c'),
                        P(f"₹{price:,.2f}", 'td_r'),
                        P(f"₹{tax:,.2f}", 'td_r'),
                        P(f"₹{half:,.2f}<br/><font size='6'>( {rate/2}% )</font>", 'td_r'),
                        P(f"₹{half:,.2f}<br/><font size='6'>( {rate/2}% )</font>", 'td_r'),
                        P(f"₹{total:,.2f}", 'td_r'),
                    ]
                else:
                    row = [
                        P(str(idx), 'td_c'),
                        prod,
                        P(item.get('hsn_code') or '-', 'td_c'),
                        P(str(item['quantity']), 'td_c'),
                        P(f"₹{price:,.2f}", 'td_r'),
                        P(f"₹{tax:,.2f}", 'td_r'),
                        P(f"₹{gst:,.2f}<br/><font size='6'>( {rate}% )</font>", 'td_r'),
                        P('', 'td_c'),
                        P(f"₹{total:,.2f}", 'td_r'),
                    ]
                rows.append(row)

            tot_qty = sum(Decimal(str(item['quantity'])) for item in items)
            if is_intra:
                total_row = [
                    P('', 'td_c'),
                    P('<b>TOTAL</b>', 'td_r'),
                    P('', 'td_c'),
                    P(f"<b>{int(tot_qty)}</b>", 'td_c'),
                    P('', 'td_c'),
                    P(f"<b>₹{tot_taxable:,.2f}</b>", 'td_r'),
                    P(f"<b>₹{tot_gst/2:,.2f}</b>", 'td_r'),
                    P(f"<b>₹{tot_gst/2:,.2f}</b>", 'td_r'),
                    P(f"<b>₹{tot_taxable + tot_gst:,.2f}</b>", 'td_r'),
                ]
            else:
                total_row = [
                    P('', 'td_c'),
                    P('<b>TOTAL</b>', 'td_r'),
                    P('', 'td_c'),
                    P(f"<b>{int(tot_qty)}</b>", 'td_c'),
                    P('', 'td_c'),
                    P(f"<b>₹{tot_taxable:,.2f}</b>", 'td_r'),
                    P(f"<b>₹{tot_gst:,.2f}</b>", 'td_r'),
                    P('', 'td_c'),
                    P(f"<b>₹{tot_taxable + tot_gst:,.2f}</b>", 'td_r'),
                ]
            rows.append(total_row)

            tbl = Table(rows, colWidths=cw_mm, repeatRows=1)
            tbl.setStyle(TableStyle([
                ('BACKGROUND',    (0,0),(-1,0),  PRIMARY),
                ('TEXTCOLOR',     (0,0),(-1,0),  WHITE),
                ('ROWBACKGROUNDS',(0,1),(-1,-1), [WHITE, LIGHT_BG]),
                ('GRID',          (0,0),(-1,-1), 0.1, colors.grey),
                ('VALIGN',        (0,0),(-1,-1), 'MIDDLE'),
                ('TOPPADDING',    (0,0),(-1,-1), 6),
                ('BOTTOMPADDING', (0,0),(-1,-1), 6),
                ('LEFTPADDING',   (0,0),(-1,-1), 4),
                ('RIGHTPADDING',  (0,0),(-1,-1), 4),
            ]))
            elements += [tbl, Spacer(1, 5)]

            # ── TOTALS ────────────────────────────────────────────────────────
            shipping = Decimal(str(order.get('shipping_charge') or 0))
            discount = Decimal(str(order.get('discount_amount') or 0))
            grand    = Decimal(str(order.get('total_amount')   or 0))

            tot_rows = []
            if discount > 0:
                tot_rows.append([P("Discount:", 'tot_lbl'), P(f"-₹{discount:,.2f}", 'tot_val')])
            tot_rows.append([P("Shipping Charges:", 'tot_lbl'), P(f"₹{shipping:,.2f}", 'tot_val')])

            tot_tbl = Table(tot_rows, colWidths=[80*mm, 40*mm])
            tot_tbl.setStyle(TableStyle([
                ('ALIGN',         (0,0),(-1,-1), 'RIGHT'),
                ('TOPPADDING',    (0,0),(-1,-1), 2),
                ('BOTTOMPADDING', (0,0),(-1,-1), 2),
            ]))

            grand_tbl = Table(
                [[P("GRAND TOTAL", 'gtotal_lbl'), P(f"₹{grand:,.2f}", 'gtotal')]],
                colWidths=[80*mm, 40*mm]
            )
            grand_tbl.setStyle(TableStyle([
                ('BACKGROUND',    (0,0),(-1,-1), PRIMARY),
                ('TOPPADDING',    (0,0),(-1,-1), 8),
                ('BOTTOMPADDING', (0,0),(-1,-1), 8),
                ('LEFTPADDING',   (0,0),(-1,-1), 8),
                ('RIGHTPADDING',  (0,0),(-1,-1), 8),
            ]))

            amount_in_words = number_to_words_indian(float(grand))
        
            summary = Table(
                [["", tot_tbl], 
                 ["", grand_tbl]],
                colWidths=[PAGE_W - 120*mm, 120*mm]
            )
            summary.setStyle(TableStyle([
                ('VALIGN', (1,0), (1,1), 'TOP'),
            ]))
            elements += [summary, Spacer(1, 15)]
        
            elements += [P(f"<b>Amount in Words:</b>{amount_in_words}", 'addr'), Spacer(1, 3)]

            # ── FOOTER: payment + terms ───────────────────────────────────────
            order_status = str(order.get('status', '')).lower()
            payment_status = str(order.get('payment_status', '')).lower()
            payment_method = str(order.get('payment_method', '—')).upper()
        
            if payment_status in ('completed', 'success', 'paid'):
                status_txt = 'Paid'
            else:
                status_txt = 'Pending'
                
            payment_info = f"Mode of payment: {payment_method}<br/>Status: {status_txt}"
        
            if payment_method != 'COD' and order.get('transaction_id'):
                payment_info += f"<br/>Payment Transaction ID: {order.get('transaction_id')}"
            elif payment_method != 'COD' and order.get('razorpay_payment_id'):
                payment_info += f"<br/>Payment Transaction ID: {order.get('razorpay_payment_id')}"

            hw = PAGE_W / 2
            footer = Table([
                [P("<b>PAYMENT INFORMATION</b>", 'label'), P("<b>TERMS &amp; CONDITIONS</b>", 'label')],
                [
                    P(payment_info, 'addr'),
                    P("1. Goods once sold will not be taken back.<br/>"
                      "2. Computer-generated invoice; no physical signature required.<br/>"
                      "3. Subject to jurisdiction of local courts.", 'addr'),
                ],
            ], colWidths=[hw, hw])
            footer.setStyle(TableStyle([
                ('VALIGN',        (0,0),(-1,-1), 'TOP'),
                ('TOPPADDING',    (0,0),(-1,-1), 5),
                ('BOTTOMPADDING', (0,0),(-1,-1), 5),
                ('BACKGROUND',    (0,0), (-1,0), LIGHT_BG),
            ]))
            elements += [footer, Spacer(1, 28)]

            # ── SIGNATURE ─────────────────────────────────────────────────────
            sig = Table(
                [["", P(f"For <b>{company['company_name']}</b><br/><br/><br/><br/>Authorized Signatory", 'sign')]],
                colWidths=[PAGE_W * 0.6, PAGE_W * 0.4]
            )
            sig.setStyle(TableStyle([
                ('ALIGN',  (1,0),(1,0), 'CENTER'),
                ('VALIGN', (0,0),(-1,-1), 'BOTTOM'),
            ]))
            elements.append(sig)

            # ── BUILD ─────────────────────────────────────────────────────────
            all_elements.extend(elements)  # type: ignore

        doc.build(all_elements)  # type: ignore
        buffer.seek(0)
        if _own_conn:
            try: _own_conn.close()
            except: pass
        return buffer, None

    except Exception as e:
        import traceback; traceback.print_exc()
        return None, str(e)

def calculate_gst_breakdown(order_id, conn):
    """Calculate GST breakdown for an order (item-wise and total)"""
    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT gstin, state_code FROM company_info LIMIT 1")
        company = cursor.fetchone()
        if not company or not company.get('gstin') or not company.get('state_code'):
            raise Exception("Company GST details not configured")

        cursor.execute("""
            SELECT o.*, u.gstin,
                JSON_EXTRACT(o.billing_address, '$.state_code') as billing_state_code,
                o.billing_address
            FROM orders o JOIN users u ON o.user_id=u.id WHERE o.id=%s
        """, (order_id,))
        order = cursor.fetchone()
        if not order:
            raise Exception("Order not found")

        raw_cust = order.get('billing_state_code', '')
        if not raw_cust and order.get('billing_address'):
            try:
                raw_cust = json.loads(order['billing_address']).get('state_code', '')
            except Exception:
                pass
        company_state  = str(company.get('state_code', '')).strip().strip('"\'')
        customer_state = str(raw_cust).strip().strip('"\'')
        is_intra = (company_state == customer_state)

        breakdown = {
            'items': [], 'is_intra_state': is_intra,
            'total_item_quantity': Decimal(0), 'total_price': Decimal(0),
            'total_item_discount': Decimal(0), 'total_taxable_value': Decimal(0),
            'total_cgst': Decimal(0), 'total_sgst': Decimal(0),
            'total_igst': Decimal(0), 'total_gst': Decimal(0),
            'company_gstin': company.get('gstin',''),
            'customer_gstin': order.get('gstin',''),
            'company_state_code': company.get('state_code',''),
            'customer_state_code': customer_state,
        }

        # If historical GST columns are populated, return them directly to prevent recalculation drift!
        if order.get('taxable_amount') is not None and float(order.get('taxable_amount')) > 0:
            cursor.execute("""
                SELECT oi.*, COALESCE(oi.price, 0) as unit_price
                FROM order_items oi
                WHERE oi.order_id = %s
            """, (order_id,))
            items = cursor.fetchall()
            
            breakdown['total_item_discount'] = Decimal(str(order.get('discount_amount') or 0))
            breakdown['total_taxable_value'] = Decimal(str(order.get('taxable_amount') or 0))
            breakdown['total_cgst'] = Decimal(str(order.get('cgst_amount') or 0))
            breakdown['total_sgst'] = Decimal(str(order.get('sgst_amount') or 0))
            breakdown['total_igst'] = Decimal(str(order.get('igst_amount') or 0))
            breakdown['total_gst'] = Decimal(str(order.get('total_gst') or 0))

            subtotal = Decimal(str(order.get('subtotal') or 0))
            discount_percentage = Decimal(0)
            if breakdown['total_item_discount'] > 0 and subtotal > 0:
                discount_percentage = (breakdown['total_item_discount'] / subtotal) * Decimal(100)
            
            for item in items:
                item_qty = Decimal(str(item.get('quantity', 1)))
                item_price = Decimal(str(item.get('price', 0)))
                gst_rate = Decimal(str(item.get('gst_rate', 18))) if item.get('gst_rate') is not None else Decimal('18.00')
                taxable_value = Decimal(str(item.get('taxable_value', 0))) if item.get('taxable_value') is not None else Decimal('0.00')
                cgst = Decimal(str(item.get('cgst_amount', 0))) if item.get('cgst_amount') is not None else Decimal('0.00')
                sgst = Decimal(str(item.get('sgst_amount', 0))) if item.get('sgst_amount') is not None else Decimal('0.00')
                igst = Decimal(str(item.get('igst_amount', 0))) if item.get('igst_amount') is not None else Decimal('0.00')
                item_discount = (item_price * item_qty) * (discount_percentage / Decimal(100))
                
                breakdown['items'].append({
                    'product_id': item.get('product_id'),
                    'product_name': item.get('product_name'),
                    'hsn_code': item.get('hsn_code'),
                    'quantity': float(item_qty), 'unit_price': float(item_price),
                    'item_discount': float(item_discount), 'taxable_value': float(taxable_value),
                    'gst_rate': float(gst_rate),
                    'cgst': float(cgst), 'sgst': float(sgst), 'igst': float(igst),
                    'total_gst': float(cgst+sgst+igst),
                    'total_value': float(taxable_value+cgst+sgst+igst),
                })
                breakdown['total_item_quantity'] += item_qty
                breakdown['total_price'] += item_price * item_qty

            for k in breakdown:
                if isinstance(breakdown[k], Decimal):
                    breakdown[k] = float(str(breakdown[k]))  # type: ignore
            return breakdown

        cursor.execute("""
            SELECT oi.*, p.gst_rate, p.gst_rate as tax_value, p.hsn_code,
                   COALESCE(oi.price, 0) as unit_price
            FROM order_items oi JOIN products p ON oi.product_id=p.id
            WHERE oi.order_id=%s
        """, (order_id,))
        items = cursor.fetchall()

        disc_pct = Decimal(0)
        if order.get('discount_amount', 0) > 0 and order.get('subtotal', 0) > 0:
            disc_pct = (Decimal(str(order['discount_amount'])) /
                        Decimal(str(order['subtotal']))) * 100

        print(f"GST state check — company: {company_state}, customer: {customer_state}, intra: {is_intra}")

        for item in items:
            tv      = Decimal(str(item.get('tax_value', 0)))
            up      = Decimal(str(item.get('price', 0)))
            qty     = Decimal(str(item.get('quantity', 1)))
            rate    = Decimal(str(item.get('gst_rate', 18) or 18))
            disc    = (tv * qty) * (disc_pct / 100)
            taxable = (tv * qty - disc) / (1 + rate / 100)

            if is_intra:
                cgst = (taxable * (rate / 2)) / 100
                sgst = cgst
                igst = Decimal(0)
            else:
                cgst = sgst = Decimal(0)
                igst = (taxable * rate) / 100

            breakdown['items'].append({
                'product_id': item.get('product_id'),
                'product_name': item.get('product_name'),
                'hsn_code': item.get('hsn_code'),
                'quantity': float(qty), 'unit_price': float(up),
                'item_discount': float(disc), 'taxable_value': float(taxable),
                'gst_rate': float(rate),
                'cgst': float(cgst), 'sgst': float(sgst), 'igst': float(igst),
                'total_gst': float(cgst+sgst+igst),
                'total_value': float(taxable+cgst+sgst+igst),
            })
            breakdown['total_item_quantity']  += qty
            breakdown['total_price']          += up
            breakdown['total_item_discount']  += disc
            breakdown['total_taxable_value']  += taxable
            breakdown['total_cgst']           += cgst
            breakdown['total_sgst']           += sgst
            breakdown['total_igst']           += igst
            breakdown['total_gst']            += (cgst+sgst+igst)

        for k in breakdown:
            if isinstance(breakdown[k], Decimal):
                breakdown[k] = float(str(breakdown[k]))  # type: ignore

        return breakdown

    except Exception as e:
        print(f"Error calculating GST breakdown: {str(e)}")
        return {
            'items': [], 'is_intra_state': False,
            'total_item_quantity': 0, 'total_price': 0,
            'total_item_discount': 0, 'total_taxable_value': 0,
            'total_cgst': 0, 'total_sgst': 0, 'total_igst': 0, 'total_gst': 0,
            'company_gstin': '', 'customer_gstin': '',
            'company_state_code': '', 'customer_state_code': '',
        }